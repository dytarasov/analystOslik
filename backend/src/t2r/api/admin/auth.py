from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Request, Response

from t2r.api.cookies import set_cookie
from t2r.api.deps import ADMIN_COOKIE, AdminDep
from t2r.domain.models.auth import LoginRequest, LoginResponse
from t2r.errors import UnauthorizedError
from t2r.infra.rate_limit.limiter import limiter
from t2r.logging import get_logger
from t2r.services.auth_service import AuthService
from t2r.settings import Settings, get_settings

router = APIRouter(prefix="/api/admin/auth", tags=["admin-auth"])
logger = get_logger("admin-auth")


@router.post("/login", response_model=LoginResponse)
@limiter.limit(lambda: get_settings().admin_login_rate_limit)
@inject
async def login(
    request: Request,
    payload: LoginRequest,
    response: Response,
    auth: FromDishka[AuthService],
    settings: FromDishka[Settings],
) -> LoginResponse:
    try:
        token, expires_at = auth.login(payload.login, payload.password)
    except UnauthorizedError:
        logger.warning("admin login failed", login=payload.login)
        raise
    set_cookie(response, ADMIN_COOKIE, token, max_age=settings.jwt_ttl_seconds)
    logger.info("admin login ok", login=payload.login)
    return LoginResponse(login=payload.login, expires_at=expires_at)


@router.post("/logout", status_code=204)
async def logout(response: Response) -> Response:
    response.delete_cookie(ADMIN_COOKIE, path="/")
    response.status_code = 204
    return response


@router.get("/me")
async def me(login: str = AdminDep) -> dict:
    return {"login": login}
