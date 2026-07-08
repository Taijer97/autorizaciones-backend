import os
import shutil
import urllib.request
import json
import io
import zipfile
from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Form, File, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import delete
from app.database import get_db
from app.models import Authorization, User, Sede
from app.schemas import AuthorizationResponse
from app.auth import get_current_user, check_permission, get_current_admin
from app.websocket import manager
from app.config import settings

router = APIRouter(prefix="/api/authorizations", tags=["authorizations"])

# Helper function to compute discount end date
def calculate_discount_term(start_month: int, start_year: int, num_cuotas: int):
    total_months_index = (start_month - 1) + num_cuotas - 1
    term_month = (total_months_index % 12) + 1
    term_year = start_year + (total_months_index // 12)
    return term_month, term_year

# Helper function to save files
def save_file(upload_file: UploadFile, auth_id: int, doc_type: str) -> str:
    auth_dir = os.path.join(settings.UPLOAD_DIR, str(auth_id))
    os.makedirs(auth_dir, exist_ok=True)
    
    _, ext = os.path.splitext(upload_file.filename)
    if not ext:
        ext = ".pdf"
    ext = ext.lower()
    
    filename = f"{doc_type}{ext}"
    file_path = os.path.join(auth_dir, filename)
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(upload_file.file, buffer)
        
    return f"uploads/{auth_id}/{filename}"

# Helper function to delete old files if exist
def delete_file_from_disk(relative_path: str):
    if not relative_path:
        return
    parts = relative_path.split("/")
    if len(parts) >= 3:
        local_path = os.path.join(settings.UPLOAD_DIR, parts[-2], parts[-1])
        if os.path.exists(local_path):
            try:
                os.remove(local_path)
            except Exception as e:
                print(f"Error borrando archivo de disco: {e}")

@router.get("/", response_model=List[AuthorizationResponse])
async def get_authorizations(
    dni: Optional[str] = None,
    sede_id: Optional[int] = None,
    doc_status: Optional[str] = None, # 'complete', 'missing_principal', 'missing_others'
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    stmt = select(Authorization)
    
    # Sede visibility logic: Non-admins can only see their assigned sedes
    if current_user.role not in ["superadmin", "admin"]:
        user_sede_ids = [s.id for s in current_user.sedes]
        stmt = stmt.where(Authorization.sede_id.in_(user_sede_ids))
        
    # Optional Sede ID filter
    if sede_id is not None:
        stmt = stmt.where(Authorization.sede_id == sede_id)
        
    # Optional DNI filter
    if dni:
        stmt = stmt.where(Authorization.dni.like(f"%{dni}%"))
        
    result = await db.execute(stmt)
    auths = result.scalars().all()
    
    # Apply doc status filtering in Python (now covers 5 files!)
    filtered_auths = []
    for auth in auths:
        has_principal = bool(auth.autorizacion_principal)
        has_duplicado = bool(auth.autorizacion_duplicado)
        has_respaldo = bool(auth.autorizacion_respaldo)
        has_declaracion = bool(auth.declaracion_jurada)
        has_dni = bool(auth.copia_dni)
        is_complete = has_principal and has_duplicado and has_respaldo and has_declaracion and has_dni
        
        if doc_status:
            if doc_status == "complete" and not is_complete:
                continue
            elif doc_status == "missing_principal" and has_principal:
                continue
            elif doc_status == "missing_others" and (not has_principal or is_complete):
                continue
                
        filtered_auths.append(auth)
        
    return filtered_auths

@router.get("/check/{dni}")
async def check_dni_documents(
    dni: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    stmt = select(Authorization).where(Authorization.dni == dni)
    
    # Filter by user's assigned sedes
    if current_user.role not in ["superadmin", "admin"]:
        user_sede_ids = [s.id for s in current_user.sedes]
        stmt = stmt.where(Authorization.sede_id.in_(user_sede_ids))
        
    result = await db.execute(stmt)
    auths = result.scalars().all()
    
    if not auths:
        return {
            "dni": dni,
            "registered": False,
            "authorizations": []
        }
        
    auth_list = []
    for auth in auths:
        has_principal = bool(auth.autorizacion_principal)
        has_duplicado = bool(auth.autorizacion_duplicado)
        has_respaldo = bool(auth.autorizacion_respaldo)
        has_declaracion = bool(auth.declaracion_jurada)
        has_dni = bool(auth.copia_dni)
        
        status_text = "Completo"
        severity = "success"
        missing_docs = []
        
        if not has_principal:
            status_text = "Falta Autorización Principal (Crítico)"
            severity = "danger"
            missing_docs.append("Autorización Principal")
        
        if not has_duplicado:
            missing_docs.append("Autorización Duplicado")
        if not has_respaldo:
            missing_docs.append("Autorización de Respaldo")
        if not has_declaracion:
            missing_docs.append("Declaración Jurada")
        if not has_dni:
            missing_docs.append("Copia DNI")
            
        if has_principal and len(missing_docs) > 0:
            status_text = "Faltan documentos secundarios"
            severity = "warning"
            
        auth_list.append({
            "id": auth.id,
            "sede": auth.sede,
            "status": status_text,
            "severity": severity,
            "missing_documents": missing_docs,
            "documents": {
                "principal": auth.autorizacion_principal,
                "duplicado": auth.autorizacion_duplicado,
                "respaldo": auth.autorizacion_respaldo,
                "declaracion": auth.declaracion_jurada,
                "copia_dni": auth.copia_dni
            },
            "apellidos_nombres": auth.apellidos_nombres,
            "inicio_descuento": f"{auth.inicio_descuento_mes:02d}/{auth.inicio_descuento_anio}",
            "termino_descuento": f"{auth.termino_descuento_mes:02d}/{auth.termino_descuento_anio}",
            "monto_mensual": float(auth.monto_mensual),
            "monto_total": float(auth.monto_total),
            "num_cuotas": auth.num_cuotas
        })
        
    return {
        "dni": dni,
        "registered": True,
        "authorizations": auth_list
    }

@router.get("/reniec/{dni}")
def get_reniec_data(
    dni: str,
    current_user: User = Depends(get_current_user)
):
    url = f"{settings.RENIEC_API_URL}/{dni}?token={settings.RENIEC_API_TOKEN}"
    try:
        req = urllib.request.Request(
            url, 
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                data = json.loads(response.read().decode('utf-8'))
                return data
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Error al consultar RENIEC"
                )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error de conexión con RENIEC: {str(e)}"
        )

@router.get("/admin/export/zip")
async def export_authorizations_zip(
    doc_type: Optional[str] = None, # 'all', 'principal', 'duplicado', 'respaldo', 'declaracion', 'copia_dni'
    status_filter: Optional[str] = None, # 'all', 'complete', 'expired', 'expiring'
    sede_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
):
    stmt = select(Authorization)
    if sede_id is not None:
        stmt = stmt.where(Authorization.sede_id == sede_id)
        
    result = await db.execute(stmt)
    auths = result.scalars().all()
    
    current_date = datetime.now()
    current_year = current_date.year
    current_month = current_date.month
    
    filtered_auths = []
    for auth in auths:
        # Expiration logic
        diff_months = (auth.termino_descuento_anio - current_year) * 12 + (auth.termino_descuento_mes - current_month)
        
        # Files availability
        has_p = bool(auth.autorizacion_principal)
        has_d = bool(auth.autorizacion_duplicado)
        has_r = bool(auth.autorizacion_respaldo)
        has_dec = bool(auth.declaracion_jurada)
        has_dni = bool(auth.copia_dni)
        is_complete = has_p and has_d and has_r and has_dec and has_dni
        
        if status_filter == 'ok' and diff_months <= 1:
            continue
        elif status_filter == 'expired' and diff_months >= 0:
            continue
        elif status_filter == 'expiring' and (diff_months < 0 or diff_months > 1):
            continue
            
        filtered_auths.append(auth)
        
    if not filtered_auths:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No se encontraron registros que coincidan con los filtros seleccionados."
        )
        
    # Generate ZIP in memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as zip_file:
        for auth in filtered_auths:
            files_to_add = {}
            if doc_type == 'all' or not doc_type:
                if auth.autorizacion_principal: files_to_add["1_Principal"] = auth.autorizacion_principal
                if auth.autorizacion_duplicado: files_to_add["2_Duplicado"] = auth.autorizacion_duplicado
                if auth.autorizacion_respaldo: files_to_add["3_Respaldo"] = auth.autorizacion_respaldo
                if auth.declaracion_jurada: files_to_add["4_Declaracion"] = auth.declaracion_jurada
                if auth.copia_dni: files_to_add["5_DNI"] = auth.copia_dni
            elif doc_type == 'principal' and auth.autorizacion_principal:
                files_to_add["1_Principal"] = auth.autorizacion_principal
            elif doc_type == 'duplicado' and auth.autorizacion_duplicado:
                files_to_add["2_Duplicado"] = auth.autorizacion_duplicado
            elif doc_type == 'respaldo' and auth.autorizacion_respaldo:
                files_to_add["3_Respaldo"] = auth.autorizacion_respaldo
            elif doc_type == 'declaracion' and auth.declaracion_jurada:
                files_to_add["4_Declaracion"] = auth.declaracion_jurada
            elif doc_type == 'copia_dni' and auth.copia_dni:
                files_to_add["5_DNI"] = auth.copia_dni
                
            # Define friendly suffixes for labels when exporting all docs
            label_suffixes = {
                "1_Principal": "AUTORIZACION PRINCIPAL",
                "2_Duplicado": "AUTORIZACION DUPLICADO",
                "3_Respaldo": "AUTORIZACION RESPALDO",
                "4_Declaracion": "DECLARACION JURADA",
                "5_DNI": "COPIA DNI"
            }

            for label, relative_path in files_to_add.items():
                parts = relative_path.split("/")
                if len(parts) >= 3:
                    file_path_on_disk = os.path.join(settings.UPLOAD_DIR, parts[-2], parts[-1])
                    if os.path.exists(file_path_on_disk):
                        # Clean name: remove hyphens, underscores and normalize spaces
                        raw_name = auth.apellidos_nombres.replace("-", " ").replace("_", " ")
                        clean_name = " ".join(raw_name.split()).upper()
                        
                        _, ext = os.path.splitext(parts[-1])
                        
                        if doc_type == 'all' or not doc_type:
                            suffix = label_suffixes.get(label, label.upper())
                            arcname = f"{auth.sede}/{clean_name} {suffix}{ext}"
                        else:
                            arcname = f"{auth.sede}/{clean_name}{ext}"
                            
                        zip_file.write(file_path_on_disk, arcname)
                        
    zip_buffer.seek(0)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"Export_CB_{timestamp}.zip"
    
    return StreamingResponse(
        zip_buffer,
        media_type="application/x-zip-compressed",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Access-Control-Expose-Headers": "Content-Disposition"
        }
    )

@router.get("/{auth_id}", response_model=AuthorizationResponse)
async def get_authorization_by_id(
    auth_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    stmt = select(Authorization).where(Authorization.id == auth_id)
    
    if current_user.role != "admin":
        user_sede_ids = [s.id for s in current_user.sedes]
        stmt = stmt.where(Authorization.sede_id.in_(user_sede_ids))
        
    result = await db.execute(stmt)
    auth = result.scalars().first()
    if not auth:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Autorización no encontrada o no tienes acceso a ella."
        )
    return auth

@router.post("/", response_model=AuthorizationResponse, status_code=status.HTTP_201_CREATED)
async def create_authorization(
    dni: str = Form(...),
    apellidos_nombres: str = Form(...),
    sede_id: int = Form(...),
    inicio_descuento_mes: int = Form(...),
    inicio_descuento_anio: int = Form(...),
    num_cuotas: int = Form(...),
    monto_mensual: float = Form(...),
    file_principal: Optional[UploadFile] = File(None),
    file_duplicado: Optional[UploadFile] = File(None),
    file_respaldo: Optional[UploadFile] = File(None),
    file_declaracion: Optional[UploadFile] = File(None),
    file_dni: Optional[UploadFile] = File(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(check_permission("create"))
):
    if current_user.role not in ["superadmin", "admin"]:
        user_sede_ids = [s.id for s in current_user.sedes]
        if sede_id not in user_sede_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes autorización para crear registros en esta sede."
            )
            
    stmt_sede = select(Sede).where(Sede.id == sede_id)
    sede_exists = (await db.execute(stmt_sede)).scalars().first()
    if not sede_exists:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La sede seleccionada no existe."
        )

    term_month, term_year = calculate_discount_term(
        inicio_descuento_mes, inicio_descuento_anio, num_cuotas
    )
    monto_total = Decimal(str(monto_mensual)) * num_cuotas
    
    db_auth = Authorization(
        dni=dni,
        apellidos_nombres=apellidos_nombres,
        sede_id=sede_id,
        inicio_descuento_mes=inicio_descuento_mes,
        inicio_descuento_anio=inicio_descuento_anio,
        num_cuotas=num_cuotas,
        termino_descuento_mes=term_month,
        termino_descuento_anio=term_year,
        monto_mensual=Decimal(str(monto_mensual)),
        monto_total=monto_total,
        created_by_id=current_user.id,
        updated_by_id=current_user.id
    )
    db.add(db_auth)
    await db.commit()
    await db.refresh(db_auth)
    
    updated = False
    if file_principal and file_principal.filename:
        db_auth.autorizacion_principal = save_file(file_principal, db_auth.id, "principal")
        updated = True
    if file_duplicado and file_duplicado.filename:
        db_auth.autorizacion_duplicado = save_file(file_duplicado, db_auth.id, "duplicado")
        updated = True
    if file_respaldo and file_respaldo.filename:
        db_auth.autorizacion_respaldo = save_file(file_respaldo, db_auth.id, "respaldo")
        updated = True
    if file_declaracion and file_declaracion.filename:
        db_auth.declaracion_jurada = save_file(file_declaracion, db_auth.id, "declaracion")
        updated = True
    if file_dni and file_dni.filename:
        db_auth.copia_dni = save_file(file_dni, db_auth.id, "dni")
        updated = True
        
    if updated:
        await db.commit()
        await db.refresh(db_auth)
        
    await manager.publish_event("AUTHORIZATION_CREATED", {
        "id": db_auth.id,
        "dni": db_auth.dni,
        "apellidos_nombres": db_auth.apellidos_nombres,
        "sede": db_auth.sede,
        "by": current_user.full_name
    })
    
    return db_auth

@router.put("/{auth_id}", response_model=AuthorizationResponse)
async def update_authorization(
    auth_id: int,
    dni: Optional[str] = Form(None),
    apellidos_nombres: Optional[str] = Form(None),
    sede_id: Optional[int] = Form(None),
    inicio_descuento_mes: Optional[int] = Form(None),
    inicio_descuento_anio: Optional[int] = Form(None),
    num_cuotas: Optional[int] = Form(None),
    monto_mensual: Optional[float] = Form(None),
    file_principal: Optional[UploadFile] = File(None),
    file_duplicado: Optional[UploadFile] = File(None),
    file_respaldo: Optional[UploadFile] = File(None),
    file_declaracion: Optional[UploadFile] = File(None),
    file_dni: Optional[UploadFile] = File(None),
    delete_principal: bool = Form(False),
    delete_duplicado: bool = Form(False),
    delete_respaldo: bool = Form(False),
    delete_declaracion: bool = Form(False),
    delete_dni: bool = Form(False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(check_permission("update"))
):
    stmt = select(Authorization).where(Authorization.id == auth_id)
    result = await db.execute(stmt)
    db_auth = result.scalars().first()
    if not db_auth:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Autorización no encontrada"
        )
        
    if current_user.role not in ["superadmin", "admin"]:
        user_sede_ids = [s.id for s in current_user.sedes]
        if db_auth.sede_id not in user_sede_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes acceso a los registros de esta sede."
            )
        if sede_id is not None and sede_id not in user_sede_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permiso de traslado de sede a una no asignada."
            )

    if dni is not None:
        db_auth.dni = dni
    if apellidos_nombres is not None:
        db_auth.apellidos_nombres = apellidos_nombres
    if sede_id is not None:
        stmt_sede = select(Sede).where(Sede.id == sede_id)
        sede_exists = (await db.execute(stmt_sede)).scalars().first()
        if not sede_exists:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="La sede seleccionada no existe."
            )
        db_auth.sede_id = sede_id
        
    mes = inicio_descuento_mes if inicio_descuento_mes is not None else db_auth.inicio_descuento_mes
    anio = inicio_descuento_anio if inicio_descuento_anio is not None else db_auth.inicio_descuento_anio
    cuotas = num_cuotas if num_cuotas is not None else db_auth.num_cuotas
    monto = Decimal(str(monto_mensual)) if monto_mensual is not None else db_auth.monto_mensual
    
    if inicio_descuento_mes is not None or inicio_descuento_anio is not None or num_cuotas is not None:
        db_auth.inicio_descuento_mes = mes
        db_auth.inicio_descuento_anio = anio
        db_auth.num_cuotas = cuotas
        
        term_month, term_year = calculate_discount_term(mes, anio, cuotas)
        db_auth.termino_descuento_mes = term_month
        db_auth.termino_descuento_anio = term_year
        
    if monto_mensual is not None or num_cuotas is not None:
        db_auth.monto_mensual = monto
        db_auth.monto_total = monto * cuotas
        
    # Handle file deletions
    if delete_principal and db_auth.autorizacion_principal:
        delete_file_from_disk(db_auth.autorizacion_principal)
        db_auth.autorizacion_principal = None
    if delete_duplicado and db_auth.autorizacion_duplicado:
        delete_file_from_disk(db_auth.autorizacion_duplicado)
        db_auth.autorizacion_duplicado = None
    if delete_respaldo and db_auth.autorizacion_respaldo:
        delete_file_from_disk(db_auth.autorizacion_respaldo)
        db_auth.autorizacion_respaldo = None
    if delete_declaracion and db_auth.declaracion_jurada:
        delete_file_from_disk(db_auth.declaracion_jurada)
        db_auth.declaracion_jurada = None
    if delete_dni and db_auth.copia_dni:
        delete_file_from_disk(db_auth.copia_dni)
        db_auth.copia_dni = None

    # Handle file uploads
    if file_principal and file_principal.filename:
        if db_auth.autorizacion_principal:
            delete_file_from_disk(db_auth.autorizacion_principal)
        db_auth.autorizacion_principal = save_file(file_principal, db_auth.id, "principal")
    if file_duplicado and file_duplicado.filename:
        if db_auth.autorizacion_duplicado:
            delete_file_from_disk(db_auth.autorizacion_duplicado)
        db_auth.autorizacion_duplicado = save_file(file_duplicado, db_auth.id, "duplicado")
    if file_respaldo and file_respaldo.filename:
        if db_auth.autorizacion_respaldo:
            delete_file_from_disk(db_auth.autorizacion_respaldo)
        db_auth.autorizacion_respaldo = save_file(file_respaldo, db_auth.id, "respaldo")
    if file_declaracion and file_declaracion.filename:
        if db_auth.declaracion_jurada:
            delete_file_from_disk(db_auth.declaracion_jurada)
        db_auth.declaracion_jurada = save_file(file_declaracion, db_auth.id, "declaracion")
    if file_dni and file_dni.filename:
        if db_auth.copia_dni:
            delete_file_from_disk(db_auth.copia_dni)
        db_auth.copia_dni = save_file(file_dni, db_auth.id, "dni")
        
    db_auth.updated_by_id = current_user.id
    await db.commit()
    await db.refresh(db_auth)
    
    await manager.publish_event("AUTHORIZATION_UPDATED", {
        "id": db_auth.id,
        "dni": db_auth.dni,
        "apellidos_nombres": db_auth.apellidos_nombres,
        "sede": db_auth.sede,
        "by": current_user.full_name
    })
    
    return db_auth

@router.delete("/{auth_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_authorization(
    auth_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(check_permission("delete"))
):
    stmt = select(Authorization).where(Authorization.id == auth_id)
    result = await db.execute(stmt)
    db_auth = result.scalars().first()
    if not db_auth:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Autorización no encontrada"
        )
        
    if current_user.role not in ["superadmin", "admin"]:
        user_sede_ids = [s.id for s in current_user.sedes]
        if db_auth.sede_id not in user_sede_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes acceso para eliminar registros de esta sede."
            )
            
    # Delete files
    delete_file_from_disk(db_auth.autorizacion_principal)
    delete_file_from_disk(db_auth.autorizacion_duplicado)
    delete_file_from_disk(db_auth.autorizacion_respaldo)
    delete_file_from_disk(db_auth.declaracion_jurada)
    delete_file_from_disk(db_auth.copia_dni)
    
    auth_dir = os.path.join(settings.UPLOAD_DIR, str(auth_id))
    if os.path.exists(auth_dir):
        try:
            shutil.rmtree(auth_dir)
        except Exception as e:
            print(f"Error borrando directorio {auth_dir}: {e}")
            
    dni = db_auth.dni
    name = db_auth.apellidos_nombres
    sede = db_auth.sede
    
    await db.delete(db_auth)
    await db.commit()
    
    await manager.publish_event("AUTHORIZATION_DELETED", {
        "id": auth_id,
        "dni": dni,
        "apellidos_nombres": name,
        "sede": sede,
        "by": current_user.full_name
    })
    
    return None
