import bcrypt
import pytest

from t2r.errors import UnauthorizedError
from t2r.infra.security.jwt import JwtCodec
from t2r.services.auth_service import AuthService
from t2r.settings import get_settings


def _settings_with_hash(monkeypatch, password: str):
    h = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode()
    monkeypatch.setenv("T2R_ADMIN_PASSWORD_HASH", h)
    get_settings.cache_clear()  # type: ignore[attr-defined]
    return get_settings()


def test_login_success(monkeypatch):
    s = _settings_with_hash(monkeypatch, "topsecret")
    svc = AuthService(s, JwtCodec(s.jwt_secret, s.jwt_ttl_seconds))
    token, exp = svc.login(s.admin_login, "topsecret")
    assert exp > 0
    assert svc.verify(token) == s.admin_login


def test_login_bad_password(monkeypatch):
    s = _settings_with_hash(monkeypatch, "topsecret")
    svc = AuthService(s, JwtCodec(s.jwt_secret, s.jwt_ttl_seconds))
    with pytest.raises(UnauthorizedError):
        svc.login(s.admin_login, "wrong")


def test_login_bad_login(monkeypatch):
    s = _settings_with_hash(monkeypatch, "topsecret")
    svc = AuthService(s, JwtCodec(s.jwt_secret, s.jwt_ttl_seconds))
    with pytest.raises(UnauthorizedError):
        svc.login("not-admin", "topsecret")


def test_verify_garbage(monkeypatch):
    s = _settings_with_hash(monkeypatch, "topsecret")
    svc = AuthService(s, JwtCodec(s.jwt_secret, s.jwt_ttl_seconds))
    with pytest.raises(UnauthorizedError):
        svc.verify("not.a.jwt")
