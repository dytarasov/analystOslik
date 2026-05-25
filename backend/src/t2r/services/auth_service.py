from __future__ import annotations

import time

from t2r.errors import UnauthorizedError
from t2r.infra.security.jwt import JwtCodec
from t2r.infra.security.passwords import verify_password
from t2r.settings import Settings


class AuthService:
    def __init__(self, settings: Settings, jwt: JwtCodec) -> None:
        self.settings = settings
        self.jwt = jwt

    def login(self, login: str, password: str) -> tuple[str, int]:
        if login != self.settings.admin_login or not verify_password(
            password, self.settings.admin_password_hash
        ):
            raise UnauthorizedError("Неверный логин или пароль")
        token = self.jwt.encode({"sub": login, "role": "admin"})
        expires_at = int(time.time()) + self.settings.jwt_ttl_seconds
        return token, expires_at

    def verify(self, token: str) -> str:
        try:
            claims = self.jwt.decode(token)
        except Exception as exc:  # noqa: BLE001
            raise UnauthorizedError("Невалидный токен") from exc
        if claims.get("role") != "admin":
            raise UnauthorizedError("Доступ запрещён")
        return claims["sub"]
