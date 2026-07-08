from pydantic import BaseModel, Field, ConfigDict
from decimal import Decimal
from datetime import datetime
from typing import Optional, List

# Token Schemas
class Token(BaseModel):
    access_token: str
    token_type: str
    role: str
    username: str
    full_name: str
    # Permissions and sedes in token metadata for frontend config
    can_create: bool
    can_read: bool
    can_update: bool
    can_delete: bool
    sede_ids: List[int]

class TokenData(BaseModel):
    username: Optional[str] = None
    role: Optional[str] = None

# Sede Schemas
class SedeBase(BaseModel):
    name: str = Field(..., description="Nombre de la Sede")

class SedeCreate(SedeBase):
    pass

class SedeResponse(SedeBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

# User Schemas
class UserBase(BaseModel):
    username: str
    dni: str
    contact_number: Optional[str] = None
    is_authorized: bool = False
    full_name: str
    role: str
    can_create: bool = False
    can_read: bool = True
    can_update: bool = False
    can_delete: bool = False

class UserCreate(BaseModel):
    dni: str
    full_name: str
    contact_number: Optional[str] = None
    pin: str # 6-digit pin
    role: str = "user"
    is_authorized: bool = False
    can_create: bool = False
    can_read: bool = True
    can_update: bool = False
    can_delete: bool = False
    sede_ids: List[int] = []

class UserRegister(BaseModel):
    dni: str
    full_name: str
    contact_number: str
    pin: str

class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    pin: Optional[str] = None
    role: Optional[str] = None
    contact_number: Optional[str] = None
    is_authorized: Optional[bool] = None
    can_create: Optional[bool] = None
    can_read: Optional[bool] = None
    can_update: Optional[bool] = None
    can_delete: Optional[bool] = None
    sede_ids: Optional[List[int]] = None

class UserResponse(UserBase):
    id: int
    created_at: datetime
    sedes: List[SedeResponse] = []

    model_config = ConfigDict(from_attributes=True)

class LoginRequest(BaseModel):
    dni: str
    pin: str

# Authorization Schemas
class AuthorizationBase(BaseModel):
    dni: str = Field(..., description="DNI del usuario")
    apellidos_nombres: str = Field(..., description="Apellidos y nombres del usuario")
    sede_id: int = Field(..., description="ID de la sede relacionada")
    inicio_descuento_mes: int = Field(..., ge=1, le=12, description="Mes de inicio del descuento")
    inicio_descuento_anio: int = Field(..., ge=2000, description="Año de inicio del descuento")
    num_cuotas: int = Field(..., gt=0, description="Número de cuotas de descuento")
    monto_mensual: Decimal = Field(..., gt=0, description="Monto mensual a descontar")

class AuthorizationCreate(AuthorizationBase):
    pass

class AuthorizationUpdate(BaseModel):
    dni: Optional[str] = None
    apellidos_nombres: Optional[str] = None
    sede_id: Optional[int] = None
    inicio_descuento_mes: Optional[int] = Field(None, ge=1, le=12)
    inicio_descuento_anio: Optional[int] = Field(None, ge=2000)
    num_cuotas: Optional[int] = Field(None, gt=0)
    monto_mensual: Optional[Decimal] = Field(None, gt=0)

class AuthorizationResponse(AuthorizationBase):
    id: int
    sede: str # Dynamic property mapping the name of the Sede
    termino_descuento_mes: int
    termino_descuento_anio: int
    monto_total: Decimal
    autorizacion_principal: Optional[str] = None
    autorizacion_duplicado: Optional[str] = None
    autorizacion_respaldo: Optional[str] = None
    declaracion_jurada: Optional[str] = None
    copia_dni: Optional[str] = None
    fecha_registro: datetime
    fecha_actualizacion: datetime
    created_by_id: int
    updated_by_id: Optional[int] = None
    creator_name: Optional[str] = None
    updater_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
