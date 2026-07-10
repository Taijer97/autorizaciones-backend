from sqlalchemy import Column, Integer, String, Numeric, DateTime, ForeignKey, func, Boolean, Table
from sqlalchemy.orm import relationship
from app.database import Base

# Association table for many-to-many relationship between Users and Sedes
user_sedes = Table(
    "user_sedes",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("sede_id", Integer, ForeignKey("sedes.id", ondelete="CASCADE"), primary_key=True)
)

class Sede(Base):
    __tablename__ = "sedes"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, index=True, nullable=False)
    created_at = Column(DateTime, default=func.now())

    # Relationships
    users = relationship("User", secondary=user_sedes, back_populates="sedes")
    authorizations = relationship("Authorization", back_populates="sede_rel")

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    dni = Column(String(20), unique=True, index=True, nullable=False)
    contact_number = Column(String(20), nullable=True)
    is_authorized = Column(Boolean, default=False, nullable=False)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(100), nullable=False)
    role = Column(String(20), default="user", nullable=False) # 'admin' or 'user'
    created_at = Column(DateTime, default=func.now())

    # Granular CRUD permissions (used mainly when role == 'user')
    can_create = Column(Boolean, default=False, nullable=False)
    can_read = Column(Boolean, default=True, nullable=False)
    can_update = Column(Boolean, default=False, nullable=False)
    can_delete = Column(Boolean, default=False, nullable=False)

    # Relationships
    authorizations = relationship("Authorization", back_populates="creator", foreign_keys="[Authorization.created_by_id]")
    sedes = relationship("Sede", secondary=user_sedes, back_populates="users", lazy="selectin")

class Authorization(Base):
    __tablename__ = "authorizations"

    id = Column(Integer, primary_key=True, index=True)
    dni = Column(String(20), index=True, nullable=False)
    apellidos_nombres = Column(String(150), nullable=False)
    
    # Linked to Sede model instead of string
    sede_id = Column(Integer, ForeignKey("sedes.id", ondelete="RESTRICT"), nullable=False)
    
    inicio_descuento_mes = Column(Integer, nullable=False)
    inicio_descuento_anio = Column(Integer, nullable=False)
    num_cuotas = Column(Integer, nullable=False)
    
    # Automatically calculated fields (will be validated and stored)
    termino_descuento_mes = Column(Integer, nullable=False)
    termino_descuento_anio = Column(Integer, nullable=False)
    monto_mensual = Column(Numeric(10, 2), nullable=False)
    monto_total = Column(Numeric(10, 2), nullable=False)
    
    # File paths for scanned documents
    autorizacion_principal = Column(String(255), nullable=True)
    autorizacion_duplicado = Column(String(255), nullable=True)
    autorizacion_respaldo = Column(String(255), nullable=True)
    declaracion_jurada = Column(String(255), nullable=True)
    copia_dni = Column(String(255), nullable=True)
    observaciones = Column(String(500), nullable=True)
    evidencias = Column(String(255), nullable=True)
    
    fecha_registro = Column(DateTime, default=func.now())
    fecha_actualizacion = Column(DateTime, default=func.now(), onupdate=func.now())
    
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    updated_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    
    # Relationships
    creator = relationship("User", back_populates="authorizations", foreign_keys=[created_by_id], lazy="selectin")
    updater = relationship("User", foreign_keys=[updated_by_id], lazy="selectin")
    sede_rel = relationship("Sede", back_populates="authorizations", lazy="selectin")

    @property
    def sede(self) -> str:
        return self.sede_rel.name if self.sede_rel else ""

    @property
    def creator_name(self) -> str:
        return self.creator.full_name if self.creator else ""

    @property
    def updater_name(self) -> str:
        return self.updater.full_name if self.updater else ""
