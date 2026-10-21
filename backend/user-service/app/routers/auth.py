from fastapi import APIRouter, HTTPException, status

from app.config import settings
from app.dependencies import DbSession
from app.schemas import LoginRequest, RegisterRequest, TokenResponse, UserOut
from app.services import user_service
from app.tokens import create_access_token

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(data: RegisterRequest, db: DbSession):
    try:
        return user_service.register_user(db, data)
    except user_service.EmailAlreadyRegistered:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")


@router.post("/login", response_model=TokenResponse)
def login(data: LoginRequest, db: DbSession):
    try:
        user = user_service.authenticate(db, data.email, data.password)
    except user_service.InvalidCredentials:
        # Same message for unknown email and wrong password
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return TokenResponse(
        access_token=create_access_token(user.id, user.role),
        expires_in=settings.jwt_expire_minutes * 60,
    )
