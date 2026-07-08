from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import delete

from app.database import get_db
from app.models import User, Sede, user_sedes
from app.schemas import UserCreate, UserUpdate, UserResponse
from app.auth import get_current_superadmin, get_password_hash
from app.routers.auth import is_easy_pin

router = APIRouter(prefix="/api/admin/users", tags=["admin_users"])

@router.get("/", response_model=List[UserResponse])
async def get_users(
    db: AsyncSession = Depends(get_db),
    current_superadmin: User = Depends(get_current_superadmin)
):
    # Fetch all users
    stmt = select(User).order_by(User.username.asc())
    result = await db.execute(stmt)
    return result.scalars().all()

@router.post("/", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    user_in: UserCreate,
    db: AsyncSession = Depends(get_db),
    current_superadmin: User = Depends(get_current_superadmin)
):
    # Check if DNI exists
    stmt = select(User).where(User.dni == user_in.dni)
    result = await db.execute(stmt)
    if result.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El DNI ya está registrado"
        )
        
    if is_easy_pin(user_in.pin):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Por seguridad, no se permiten PINs fáciles, secuenciales o repetitivos."
        )
        
    hashed_pwd = get_password_hash(user_in.pin)
    
    # 1. Create User
    db_user = User(
        username=user_in.dni,
        dni=user_in.dni,
        contact_number=user_in.contact_number,
        is_authorized=user_in.is_authorized,
        password_hash=hashed_pwd,
        full_name=user_in.full_name,
        role=user_in.role,
        can_create=user_in.can_create if user_in.role != "admin" else True,
        can_read=user_in.can_read if user_in.role != "admin" else True,
        can_update=user_in.can_update if user_in.role != "admin" else True,
        can_delete=user_in.can_delete if user_in.role != "admin" else True,
    )
    
    # 2. Assign Sedes
    if user_in.sede_ids:
        sede_stmt = select(Sede).where(Sede.id.in_(user_in.sede_ids))
        sede_result = await db.execute(sede_stmt)
        db_user.sedes = list(sede_result.scalars().all())
        
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user

@router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    user_in: UserUpdate,
    db: AsyncSession = Depends(get_db),
    current_superadmin: User = Depends(get_current_superadmin)
):
    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    db_user = result.scalars().first()
    if not db_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuario no encontrado"
        )
        
    # Update fields
    if user_in.full_name is not None:
        db_user.full_name = user_in.full_name
    if user_in.pin is not None and user_in.pin != "":
        if is_easy_pin(user_in.pin):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Por seguridad, no se permiten PINs fáciles, secuenciales o repetitivos."
            )
        db_user.password_hash = get_password_hash(user_in.pin)
    if user_in.role is not None:
        db_user.role = user_in.role
    if user_in.contact_number is not None:
        db_user.contact_number = user_in.contact_number
    if user_in.is_authorized is not None:
        db_user.is_authorized = user_in.is_authorized
        
    # Standard permissions
    if user_in.role == "admin":
        db_user.can_create = True
        db_user.can_read = True
        db_user.can_update = True
        db_user.can_delete = True
    else:
        if user_in.can_create is not None:
            db_user.can_create = user_in.can_create
        if user_in.can_read is not None:
            db_user.can_read = user_in.can_read
        if user_in.can_update is not None:
            db_user.can_update = user_in.can_update
        if user_in.can_delete is not None:
            db_user.can_delete = user_in.can_delete
            
    # Update Sede assignments
    if user_in.sede_ids is not None:
        # Clear existing associations
        db_user.sedes = []
        if user_in.sede_ids:
            sede_stmt = select(Sede).where(Sede.id.in_(user_in.sede_ids))
            sede_result = await db.execute(sede_stmt)
            db_user.sedes = list(sede_result.scalars().all())
            
    await db.commit()
    await db.refresh(db_user)
    return db_user

@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_superadmin: User = Depends(get_current_superadmin)
):
    if user_id == current_superadmin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No puedes eliminar tu propio usuario administrador"
        )
        
    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    db_user = result.scalars().first()
    if not db_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuario no encontrado"
        )
        
    await db.delete(db_user)
    await db.commit()
    return None
