import time

import jwt as pyjwt
import pytest

from t2r.infra.security.jwt import JwtCodec


def test_encode_decode_roundtrip():
    j = JwtCodec("k" * 40, ttl_seconds=60)
    tok = j.encode({"sub": "admin"})
    decoded = j.decode(tok)
    assert decoded["sub"] == "admin"
    assert decoded["exp"] > decoded["iat"]


def test_expired_token_rejected():
    j = JwtCodec("k" * 40, ttl_seconds=-1)
    tok = j.encode({"sub": "admin"})
    with pytest.raises(pyjwt.ExpiredSignatureError):
        j.decode(tok)


def test_wrong_secret_rejected():
    a = JwtCodec("a" * 40)
    b = JwtCodec("b" * 40)
    tok = a.encode({"sub": "admin"})
    with pytest.raises(pyjwt.InvalidTokenError):
        b.decode(tok)
