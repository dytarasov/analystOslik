import bcrypt

from t2r.infra.security.passwords import verify_password


def test_verify_password_ok():
    h = bcrypt.hashpw(b"secret", bcrypt.gensalt(rounds=4)).decode()
    assert verify_password("secret", h) is True


def test_verify_password_wrong():
    h = bcrypt.hashpw(b"secret", bcrypt.gensalt(rounds=4)).decode()
    assert verify_password("other", h) is False


def test_verify_password_garbage_hash():
    assert verify_password("any", "not-a-bcrypt-hash") is False
