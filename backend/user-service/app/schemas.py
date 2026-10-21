from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class RegisterRequest(BaseModel):
    # Unknown fields (for example "role") are rejected, not silently ignored
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    full_name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=8, max_length=72)

    @field_validator("full_name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("full_name is too short")
        return value

    @field_validator("password")
    @classmethod
    def bcrypt_limit(cls, value: str) -> str:
        # bcrypt only uses the first 72 bytes (accents use 2 bytes each)
        if len(value.encode("utf-8")) > 72:
            raise ValueError("password is longer than 72 bytes")
        return value


class LoginRequest(BaseModel):
    # No format validation here: login only looks the email up.
    # The format is validated when the account is created.
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=72)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserOut(BaseModel):
    # Allows building the response directly from a SQLAlchemy object.
    # password_hash is not listed, so it can never be returned.
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str
    role: str
    is_active: bool
    created_at: datetime
