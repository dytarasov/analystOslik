"""Top-level pytest config.

Provides reasonable defaults for env vars so importing `t2r.settings` works in
unit tests that do not load `.env`.
"""
from __future__ import annotations

import os

# Set safe defaults BEFORE any t2r module is imported. Tests that need real
# values (e.g. integration) override via monkeypatch.
_DEFAULTS = {
    "T2R_ENV": "test",
    "T2R_LOG_LEVEL": "WARNING",
    "T2R_CORS_ORIGINS": "http://localhost:3000",
    "T2R_ADMIN_LOGIN": "admin",
    # bcrypt("admin")
    "T2R_ADMIN_PASSWORD_HASH": "$2b$12$h4pvfNzIQqqkjl6MSp357.i8bb8A8OMOUtQRHbRBow31VZ15quDS.",
    "T2R_JWT_SECRET": "test-jwt-secret-xxxxxxxxxxxxxxxxxxxxxxx",
    "T2R_JWT_TTL_SECONDS": "3600",
    # 32-byte Fernet key (base64)
    "T2R_ENCRYPTION_KEY": "5Pxi7ZlDrgN50T8YQD3vA20J9necZx4CINcb42hlNE4=",
    "T2R_PG_DSN": "postgresql+asyncpg://t2r:t2r@localhost:5432/t2r",
    "T2R_NEO4J_URI": "bolt://localhost:7687",
    "T2R_NEO4J_USER": "neo4j",
    "T2R_NEO4J_PASSWORD": "test",
    "T2R_LLM_BASE_URL": "http://localhost:9999/v1",
    "T2R_LLM_API_KEY": "test",
    "T2R_LLM_MODEL": "test-model",
    "T2R_EMB_BASE_URL": "http://localhost:9998/v1",
    "T2R_EMB_API_KEY": "test",
    "T2R_EMB_MODEL": "test-emb",
    "T2R_EMB_DIM": "8",
    "T2R_CH_DEFAULT_MAX_EXECUTION_TIME": "30",
    "T2R_CH_DEFAULT_LIMIT": "1000",
    "T2R_SSE_PING_INTERVAL": "15",
    "T2R_CLIENT_RATE_LIMIT": "100/minute",
    "T2R_EXPORT_DIR": "/tmp/t2r-exports",
}

for k, v in _DEFAULTS.items():
    os.environ.setdefault(k, v)
