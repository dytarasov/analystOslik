from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Cookie, Request, Response
from pydantic import BaseModel

from t2r.api.cookies import set_cookie
from t2r.api.deps import ACCESS_COOKIE
from t2r.errors import UnauthorizedError
from t2r.infra.rate_limit.limiter import limiter
from t2r.logging import get_logger
from t2r.services.auth_service import AuthService
from t2r.settings import Settings, get_settings

router = APIRouter(prefix="/api/access", tags=["client-access"])
logger = get_logger("access")


class UnlockRequest(BaseModel):
    key: str


@router.get("/status")
@inject
async def status(
    auth: FromDishka[AuthService],
    settings: FromDishka[Settings],
    t2r_access: str | None = Cookie(default=None),
) -> dict:
    """Нужен ли ключ и разблокирован ли уже этот браузер — чтобы фронт решал,
    показывать ли экран ввода ключа."""
    if not settings.access_required:
        return {"required": False, "unlocked": True}
    return {"required": True, "unlocked": auth.verify_access(t2r_access)}


@router.post("/unlock")
# Лимит читаем из настроек лениво (на момент импорта settings ещё нет).
@limiter.limit(lambda: get_settings().access_rate_limit)
@inject
async def unlock(
    request: Request,
    payload: UnlockRequest,
    response: Response,
    auth: FromDishka[AuthService],
    settings: FromDishka[Settings],
) -> dict:
    try:
        token, expires_at = auth.unlock(payload.key)
    except UnauthorizedError:
        logger.warning("access unlock failed")
        raise
    set_cookie(response, ACCESS_COOKIE, token, max_age=settings.jwt_ttl_seconds)
    logger.info("access unlocked")
    return {"ok": True, "expires_at": expires_at}
