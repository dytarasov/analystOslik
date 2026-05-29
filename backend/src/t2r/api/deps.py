from __future__ import annotations

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import Cookie, Depends

from t2r.errors import AccessRequiredError, UnauthorizedError
from t2r.services.auth_service import AuthService
from t2r.settings import Settings

ADMIN_COOKIE = "t2r_admin"
CLIENT_COOKIE = "t2r_session"
ACCESS_COOKIE = "t2r_access"


@inject
async def require_admin(
    auth: FromDishka[AuthService],
    t2r_admin: str | None = Cookie(default=None),
) -> str:
    if not t2r_admin:
        raise UnauthorizedError("Требуется авторизация")
    return auth.verify(t2r_admin)


AdminDep = Depends(require_admin)


@inject
async def require_client_access(
    auth: FromDishka[AuthService],
    settings: FromDishka[Settings],
    t2r_access: str | None = Cookie(default=None),
) -> None:
    """Гейт клиентской части по общему UUID-ключу. Если ключ не задан в настройках
    (dev) — пропускаем всех. Иначе требуем валидный gate-cookie."""
    if not settings.access_required:
        return
    if not auth.verify_access(t2r_access):
        raise AccessRequiredError("Требуется ключ доступа")


ClientAccessDep = Depends(require_client_access)
