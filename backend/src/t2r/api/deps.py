from __future__ import annotations

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import Cookie, Depends

from t2r.errors import UnauthorizedError
from t2r.services.auth_service import AuthService

ADMIN_COOKIE = "t2r_admin"
CLIENT_COOKIE = "t2r_session"


@inject
async def require_admin(
    auth: FromDishka[AuthService],
    t2r_admin: str | None = Cookie(default=None),
) -> str:
    if not t2r_admin:
        raise UnauthorizedError("Требуется авторизация")
    return auth.verify(t2r_admin)


AdminDep = Depends(require_admin)
