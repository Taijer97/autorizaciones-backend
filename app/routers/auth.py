from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.database import get_db
from app.models import User
from app.schemas import UserCreate, UserResponse, Token, LoginRequest, UserRegister
from app.auth import get_password_hash, verify_password, create_access_token, get_current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])

def is_easy_pin(pin: str) -> bool:
    # 1. Identical digits (e.g. 111111)
    if len(set(pin)) == 1:
        return True
    # 2. Sequential ascending (e.g. 123456, 456789)
    ascending = "01234567890123456"
    if pin in ascending:
        return True
    # 3. Sequential descending (e.g. 654321, 987654)
    descending = "98765432109876543210"
    if pin in descending:
        return True
    # 4. Alternating digits (e.g. 121212)
    if pin[0] == pin[2] == pin[4] and pin[1] == pin[3] == pin[5]:
        return True
    # 5. Repeated sequences (e.g. 123123)
    if pin[:3] == pin[3:]:
        return True
    return False

@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(user_in: UserRegister, db: AsyncSession = Depends(get_db)):
    # Block easy PINs
    if is_easy_pin(user_in.pin):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Por seguridad, no se permiten PINs fáciles, secuenciales o repetitivos (ej. 123456, 111111, 121212)."
        )

    # Check if user exists by DNI
    result = await db.execute(select(User).where(User.dni == user_in.dni))
    existing_user = result.scalars().first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El DNI ya se encuentra registrado"
        )
    
    # Hash the 6-digit PIN and create the user (unauthorized by default)
    hashed_pin = get_password_hash(user_in.pin)
    db_user = User(
        username=user_in.dni, # username defaults to DNI for compatibility
        dni=user_in.dni,
        contact_number=user_in.contact_number,
        password_hash=hashed_pin,
        full_name=user_in.full_name,
        role="user", # default is operator/user
        is_authorized=False, # needs admin approval!
        can_create=False,
        can_read=False,
        can_update=False,
        can_delete=False
    )
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user

@router.post("/login", response_model=Token)
async def login(login_data: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.dni == login_data.dni))
    user = result.scalars().first()
    
    if not user or not verify_password(login_data.pin, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="DNI o PIN incorrectos",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    # Check if the user has been authorized by the administrator
    if not user.is_authorized:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Su cuenta está pendiente de autorización por parte del administrador."
        )
    
    # Create token using username/dni
    access_token = create_access_token(
        data={"sub": user.username, "role": user.role}
    )
    
    # Get user's sede IDs (admins/superadmins get access to all sedes automatically)
    if user.role in ["superadmin", "admin"]:
        from app.models import Sede
        sede_result = await db.execute(select(Sede.id))
        sede_ids = [row[0] for row in sede_result.all()]
    else:
        sede_ids = [s.id for s in user.sedes]
        
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": user.role,
        "username": user.username,
        "full_name": user.full_name,
        "can_create": user.role in ["superadmin", "admin"] or user.can_create,
        "can_read": user.role in ["superadmin", "admin"] or user.can_read,
        "can_update": user.role in ["superadmin", "admin"] or user.can_update,
        "can_delete": user.role in ["superadmin", "admin"] or user.can_delete,
        "sede_ids": sede_ids
    }

@router.get("/me", response_model=UserResponse)
async def read_users_me(current_user: User = Depends(get_current_user)):
    return current_user
