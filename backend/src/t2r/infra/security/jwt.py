import time

import jwt as pyjwt


class JwtCodec:
    def __init__(self, secret: str, ttl_seconds: int = 86400) -> None:
        self._secret = secret
        self._ttl = ttl_seconds

    def encode(self, claims: dict) -> str:
        now = int(time.time())
        payload = {**claims, "iat": now, "exp": now + self._ttl}
        return pyjwt.encode(payload, self._secret, algorithm="HS256")

    def decode(self, token: str) -> dict:
        return pyjwt.decode(token, self._secret, algorithms=["HS256"])
