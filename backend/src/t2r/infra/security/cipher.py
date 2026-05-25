from cryptography.fernet import Fernet


class FernetCipher:
    def __init__(self, key: str) -> None:
        self._f = Fernet(key.encode() if isinstance(key, str) else key)

    def encrypt(self, plain: str) -> bytes:
        return self._f.encrypt(plain.encode("utf-8"))

    def decrypt(self, token: bytes) -> str:
        return self._f.decrypt(token).decode("utf-8")
