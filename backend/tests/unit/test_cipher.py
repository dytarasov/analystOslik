from cryptography.fernet import Fernet

from t2r.infra.security.cipher import FernetCipher


def test_roundtrip():
    key = Fernet.generate_key().decode()
    c = FernetCipher(key)
    enc = c.encrypt("hello мир")
    assert isinstance(enc, bytes)
    assert c.decrypt(enc) == "hello мир"


def test_different_ciphertexts_for_same_input():
    key = Fernet.generate_key().decode()
    c = FernetCipher(key)
    assert c.encrypt("x") != c.encrypt("x")  # nonce makes them differ
