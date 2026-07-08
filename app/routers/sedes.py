from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.exc import IntegrityError

from app.database import get_db
from app.models import Sede, User
from app.schemas import SedeCreate, SedeResponse
from app.auth import get_current_user, get_current_admin, get_current_superadmin

router = APIRouter(prefix="/api/sedes", tags=["sedes"])

@router.get("/", response_model=List[SedeResponse])
async def get_sedes(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role in ["superadmin", "admin"]:
        stmt = select(Sede).order_by(Sede.name.asc())
        result = await db.execute(stmt)
        return result.scalars().all()
    else:
        # Standard users only see sedes they are assigned to
        return current_user.sedes

@router.post("/", response_model=SedeResponse, status_code=status.HTTP_201_CREATED)
async def create_sede(
    sede_in: SedeCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_superadmin)
):
    # Check if Sede name exists
    stmt = select(Sede).where(Sede.name == sede_in.name)
    result = await db.execute(stmt)
    if result.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ya existe una sede con este nombre"
        )
        
    db_sede = Sede(name=sede_in.name)
    db.add(db_sede)
    await db.commit()
    await db.refresh(db_sede)
    return db_sede

@router.delete("/{sede_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sede(
    sede_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_superadmin)
):
    stmt = select(Sede).where(Sede.id == sede_id)
    result = await db.execute(stmt)
    db_sede = result.scalars().first()
    if not db_sede:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sede no encontrada"
        )
        
    try:
        await db.delete(db_sede)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No se puede eliminar la sede porque existen registros de autorización o usuarios asociados a ella."
        )
        
    return None
