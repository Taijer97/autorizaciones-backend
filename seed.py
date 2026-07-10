import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select
from sqlalchemy import text

from app.config import settings
from app.database import Base
from app.models import User, Sede
from app.auth import get_password_hash

DEFAULT_SEDES = []

async def seed():
    db_host_info = settings.DATABASE_URL.split("@")[-1]
    print(f"Conectando a la base de datos remota para migración y semilla: {db_host_info}")
    
    engine = create_async_engine(settings.DATABASE_URL, echo=True)
    
    # 1. Schema Check and DDL Migration
    async with engine.begin() as conn:
        print("Comprobando existencia de tablas...")
        
        # Check if 'users' table exists and needs permission columns
        users_res = await conn.execute(text("SHOW TABLES LIKE 'users';"))
        users_table_exists = users_res.fetchone() is not None
        if users_table_exists:
            print("La tabla 'users' ya existe. Comprobando columnas...")
            user_cols_res = await conn.execute(text("SHOW COLUMNS FROM users;"))
            user_cols = [row[0] for row in user_cols_res.fetchall()]
            if "can_create" not in user_cols:
                print("Migración de permisos en 'users' detectada. Agregando columnas...")
                await conn.execute(text("ALTER TABLE users ADD COLUMN can_create TINYINT(1) NOT NULL DEFAULT 0;"))
                await conn.execute(text("ALTER TABLE users ADD COLUMN can_read TINYINT(1) NOT NULL DEFAULT 1;"))
                await conn.execute(text("ALTER TABLE users ADD COLUMN can_update TINYINT(1) NOT NULL DEFAULT 0;"))
                await conn.execute(text("ALTER TABLE users ADD COLUMN can_delete TINYINT(1) NOT NULL DEFAULT 0;"))

            # Check for new columns: dni, contact_number, is_authorized
            user_cols_res2 = await conn.execute(text("SHOW COLUMNS FROM users;"))
            user_cols_now = [row[0] for row in user_cols_res2.fetchall()]
            if "dni" not in user_cols_now:
                print("Añadiendo columnas de DNI, contacto y autorización a la tabla 'users'...")
                await conn.execute(text("ALTER TABLE users ADD COLUMN dni VARCHAR(20) NULL;"))
                await conn.execute(text("ALTER TABLE users ADD COLUMN contact_number VARCHAR(20) NULL;"))
                await conn.execute(text("ALTER TABLE users ADD COLUMN is_authorized TINYINT(1) NOT NULL DEFAULT 0;"))
                
                # Update existing admin and user entries to have unique DNIs and be authorized
                await conn.execute(text("UPDATE users SET dni = '00000000', is_authorized = 1 WHERE username = 'admin';"))
                await conn.execute(text("UPDATE users SET dni = '11111111', is_authorized = 1 WHERE username = 'user';"))
                # Fallback for any other custom users
                await conn.execute(text("UPDATE users SET dni = CONCAT('1000000', id), is_authorized = 1 WHERE dni IS NULL;"))
                
                # Make DNI NOT NULL and UNIQUE
                await conn.execute(text("ALTER TABLE users MODIFY COLUMN dni VARCHAR(20) NOT NULL;"))
                await conn.execute(text("ALTER TABLE users ADD UNIQUE INDEX idx_users_dni (dni);"))
                print("Columnas de DNI y autorización añadidas y configuradas correctamente.")
        
        # Check if 'authorizations' table exists
        tables_res = await conn.execute(text("SHOW TABLES LIKE 'authorizations';"))
        auth_table_exists = tables_res.fetchone() is not None
        
        if auth_table_exists:
            print("La tabla 'authorizations' ya existe. Comprobando columnas...")
            columns_res = await conn.execute(text("SHOW COLUMNS FROM authorizations;"))
            columns = [row[0] for row in columns_res.fetchall()]
            
            # If 'sede_id' is missing but 'sede' string is present, perform migration
            if "sede_id" not in columns:
                print("Estructura antigua de 'authorizations' detectada. Creando 'sedes' y agregando 'sede_id'...")
                await conn.run_sync(Base.metadata.create_all)
                await conn.execute(text("ALTER TABLE authorizations ADD COLUMN sede_id INT NULL;"))
                await conn.execute(text(
                    "ALTER TABLE authorizations ADD CONSTRAINT fk_auth_sede "
                    "FOREIGN KEY (sede_id) REFERENCES sedes (id) ON DELETE RESTRICT;"
                ))
            else:
                print("Estructura moderna de 'authorizations' detectada. Verificando integridad...")
                await conn.run_sync(Base.metadata.create_all)
        else:
            print("Primera ejecución: creando todas las tablas desde cero...")
            await conn.run_sync(Base.metadata.create_all)
            
    async_session = sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False
    )
    
    async with async_session() as session:
        # 2. Seed default Sedes
        print("Verificando catálogo de sedes por defecto...")
        for name in DEFAULT_SEDES:
            result = await session.execute(select(Sede).where(Sede.name == name))
            if not result.scalars().first():
                print(f"Sembrando sede: {name}")
                session.add(Sede(name=name))
        await session.commit()
        
        # 3. Data Migration (Copy string 'sede' to 'sede_id' FK)
        if auth_table_exists:
            # Check if there is a 'sede' column in the database schema still
            async with engine.begin() as conn:
                cols_res = await conn.execute(text("SHOW COLUMNS FROM authorizations;"))
                current_cols = [r[0] for r in cols_res.fetchall()]
                has_old_sede_col = "sede" in current_cols
                
            if has_old_sede_col:
                print("Migrando valores de sede antigua (texto) a la nueva relación (sede_id)...")
                # Get unique names from authorizations
                res = await session.execute(text("SELECT DISTINCT sede FROM authorizations WHERE sede_id IS NULL AND sede IS NOT NULL;"))
                distinct_legacy_sedes = [row[0] for row in res.fetchall() if row[0]]
                
                for s_name in distinct_legacy_sedes:
                    # Find or create Sede ID for this name
                    s_res = await session.execute(select(Sede).where(Sede.name == s_name))
                    db_sede = s_res.scalars().first()
                    if not db_sede:
                        db_sede = Sede(name=s_name)
                        session.add(db_sede)
                        await session.commit()
                        await session.refresh(db_sede)
                        
                    # Update records
                    await session.execute(
                        text("UPDATE authorizations SET sede_id = :sede_id WHERE sede = :sede_name AND sede_id IS NULL"),
                        {"sede_id": db_sede.id, "sede_name": s_name}
                    )
                    await session.commit()
                
                # Check for any remaining NULL sede_ids and map them to Sede Central
                default_sede_res = await session.execute(select(Sede).where(Sede.name == 'Sede Central - Lima'))
                default_sede = default_sede_res.scalars().first()
                if default_sede:
                    await session.execute(
                        text("UPDATE authorizations SET sede_id = :sede_id WHERE sede_id IS NULL"),
                        {"sede_id": default_sede.id}
                    )
                    await session.commit()
                
                # 4. Finalize database schema (set NOT NULL and DROP old column)
                print("Finalizando migración física de base de datos...")
                async with engine.begin() as conn:
                    # Modify column to NOT NULL
                    await conn.execute(text("ALTER TABLE authorizations MODIFY COLUMN sede_id INT NOT NULL;"))
                    # Drop old text column
                    await conn.execute(text("ALTER TABLE authorizations DROP COLUMN sede;"))
                    print("Columna 'sede' heredada eliminada correctamente.")

            # Separate block to run DNI/Declaracion split migration
            async with engine.begin() as conn:
                cols_res_chk = await conn.execute(text("SHOW COLUMNS FROM authorizations;"))
                current_cols_now = [r[0] for r in cols_res_chk.fetchall()]
                
                if "declaracion_jurada_dni" in current_cols_now:
                    print("Migrando declaracion_jurada_dni a columnas separadas declaracion_jurada y copia_dni...")
                    if "declaracion_jurada" not in current_cols_now:
                        await conn.execute(text("ALTER TABLE authorizations ADD COLUMN declaracion_jurada VARCHAR(255) NULL;"))
                    if "copia_dni" not in current_cols_now:
                        await conn.execute(text("ALTER TABLE authorizations ADD COLUMN copia_dni VARCHAR(255) NULL;"))
                    
                    await conn.execute(text("UPDATE authorizations SET declaracion_jurada = declaracion_jurada_dni;"))
                    await conn.execute(text("ALTER TABLE authorizations DROP COLUMN declaracion_jurada_dni;"))
                    print("Migración de declaracion_jurada y copia_dni completada con éxito.")

                # Check for updated_by_id and add it
                if "updated_by_id" not in current_cols_now:
                    print("Añadiendo columna 'updated_by_id' a la tabla 'authorizations'...")
                    await conn.execute(text("ALTER TABLE authorizations ADD COLUMN updated_by_id INT NULL;"))
                    try:
                        await conn.execute(text("ALTER TABLE authorizations ADD CONSTRAINT fk_authorizations_updated_by FOREIGN KEY (updated_by_id) REFERENCES users(id);"))
                    except Exception as e:
                        print(f"Advertencia al agregar clave foránea fk_authorizations_updated_by: {e}")
                    await conn.execute(text("UPDATE authorizations SET updated_by_id = created_by_id;"))
                    print("Columna 'updated_by_id' añadida con éxito.")
                    
                # Check for observaciones and add it
                if "observaciones" not in current_cols_now:
                    print("Añadiendo columna 'observaciones' a la tabla 'authorizations'...")
                    await conn.execute(text("ALTER TABLE authorizations ADD COLUMN observaciones VARCHAR(500) NULL;"))
                    print("Columna 'observaciones' añadida con éxito.")
                    
                # Check for evidencias and add it
                if "evidencias" not in current_cols_now:
                    print("Añadiendo columna 'evidencias' a la tabla 'authorizations'...")
                    await conn.execute(text("ALTER TABLE authorizations ADD COLUMN evidencias VARCHAR(255) NULL;"))
                    print("Columna 'evidencias' añadida con éxito.")
        
        # 5. Fetch all sedes to assign to users
        sedes_res = await session.execute(select(Sede))
        all_sedes = list(sedes_res.scalars().all())
        
        # 6. Seed Users with permissions
        print("Verificando usuarios semilla...")
        
        # Seed Superadmin
        result = await session.execute(select(User).where(User.username == "admin"))
        superadmin_user = result.scalars().first()
        if not superadmin_user:
            print("Creando usuario superadministrador (admin / admin123)...")
            superadmin_user = User(
                username="admin",
                dni="00000000",
                is_authorized=True,
                password_hash=get_password_hash("admin123"),
                full_name="Superadministrador CB",
                role="superadmin",
                can_create=True,
                can_read=True,
                can_update=True,
                can_delete=True
            )
            superadmin_user.sedes = all_sedes
            session.add(superadmin_user)
        else:
            print("El usuario admin ya existe. Actualizando a rol superadmin...")
            superadmin_user.role = "superadmin"
            superadmin_user.can_create = True
            superadmin_user.can_read = True
            superadmin_user.can_update = True
            superadmin_user.can_delete = True
            superadmin_user.sedes = all_sedes


            
        await session.commit()
        print("Semilla de usuarios y sedes completada con éxito.")
        
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(seed())
