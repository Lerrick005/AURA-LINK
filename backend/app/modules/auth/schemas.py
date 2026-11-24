"""Request/response schemas for the auth router."""
from __future__ import annotations
from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)


class TokenPair(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str | None = None


class LoginResponse(BaseModel):
    user: UserOut
    org_id: str
    roles: list[str]
    tokens: TokenPair
