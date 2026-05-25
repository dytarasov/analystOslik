from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Response

from t2r.api.deps import ADMIN_COOKIE, AdminDep
from t2r.domain.models.auth import LoginRequest, LoginResponse
from t2r.services.auth_service import AuthService
from t2r.settings import Settings

router = APIRouter(prefix="/api/admin/auth", tags=["admin-auth"])


@router.post("/login", response_model=LoginResponse)
@inject
async def login(
    payload: LoginRequest,
    response: Response,
    auth: FromDishka[AuthService],
    settings: FromDishka[Settings],
) -> LoginResponse:
    token, expires_at = auth.login(payload.login, payload.password)
    response.set_cookie(
        ADMIN_COOKIE,
        token,
        max_age=settings.jwt_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.env == "prod",
        path="/",
    )
    return LoginResponse(login=payload.login, expires_at=expires_at)


@router.post("/logout", status_code=204)
async def logout(response: Response) -> Response:
    response.delete_cookie(ADMIN_COOKIE, path="/")
    response.status_code = 204
    return response


@router.get("/me")
async def me(login: str = AdminDep) -> dict:
    return {"login": login}
