from pydantic import BaseModel


class LoginRequest(BaseModel):
    login: str
    password: str


class LoginResponse(BaseModel):
    login: str
    expires_at: int


class AuthContext(BaseModel):
    login: str
