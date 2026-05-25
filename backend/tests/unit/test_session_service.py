"""Light test for the cookie hashing helper to ensure determinism."""
from t2r.services import session_service as ss


def test_cookie_hash_is_deterministic_and_64hex():
    h1 = ss._cookie_hash("abc")
    h2 = ss._cookie_hash("abc")
    assert h1 == h2
    assert len(h1) == 64
    assert h1 != ss._cookie_hash("xyz")


def test_new_cookie_unique_and_hex():
    a = ss.SessionService.new_cookie()
    b = ss.SessionService.new_cookie()
    assert a != b
    assert all(c in "0123456789abcdef" for c in a)
