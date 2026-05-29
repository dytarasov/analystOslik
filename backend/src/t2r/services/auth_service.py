from __future__ import annotations

import hashlib
import hmac
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

    # --- Гейт клиентской части по общему UUID-ключу ---

    def _access_fingerprint(self) -> str:
        """Отпечаток текущего ключа доступа. Кладём в токен, чтобы ротация ключа
        (смена T2R_ACCESS_KEY) мгновенно инвалидировала ранее выданные cookie."""
        return hashlib.sha256(self.settings.access_key.encode()).hexdigest()[:16]

    def unlock(self, key: str) -> tuple[str, int]:
        """Обменять введённый UUID-ключ на gate-токен. Сам ключ в cookie не
        попадает — отдаём подписанный JWT с отпечатком ключа."""
        expected = self.settings.access_key
        if not expected or not hmac.compare_digest((key or "").strip(), expected):
            raise UnauthorizedError("Неверный ключ доступа")
        token = self.jwt.encode({"sub": "client", "role": "client", "kf": self._access_fingerprint()})
        expires_at = int(time.time()) + self.settings.jwt_ttl_seconds
        return token, expires_at

    def verify_access(self, token: str | None) -> bool:
        if not token:
            return False
        try:
            claims = self.jwt.decode(token)
        except Exception:  # noqa: BLE001
            return False
        return claims.get("role") == "client" and claims.get("kf") == self._access_fingerprint()
