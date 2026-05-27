from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="T2R_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    env: str = "dev"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:3000"

    admin_login: str
    admin_password_hash: str
    jwt_secret: str
    jwt_ttl_seconds: int = 86400

    encryption_key: str

    pg_dsn: str
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str

    # Accept both T2R_LLM_API_URL (preferred) and T2R_LLM_BASE_URL (legacy).
    llm_base_url: str = Field(
        validation_alias=AliasChoices("T2R_LLM_API_URL", "T2R_LLM_BASE_URL")
    )
    llm_api_key: str
    llm_model: str
    llm_temperature: float = 0.2
    llm_max_tokens: int = 4096
    # Pin OpenRouter routing to a single upstream provider (e.g. "Friendli").
    # Empty/unset → OpenRouter picks the provider as usual. Accepts the bare
    # LLM_OPENROUTER_PROVIDER as well as the prefixed T2R_ form.
    llm_openrouter_provider: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "T2R_LLM_OPENROUTER_PROVIDER", "LLM_OPENROUTER_PROVIDER"
        ),
    )

    emb_base_url: str = Field(
        validation_alias=AliasChoices("T2R_EMB_API_URL", "T2R_EMB_BASE_URL")
    )
    emb_api_key: str
    emb_model: str
    emb_dim: int = 1536

    ch_default_max_execution_time: int = 30
    ch_default_limit: int = 10000

    sse_ping_interval: int = 15

    client_rate_limit: str = "10/minute"

    export_dir: str = "/var/t2r/exports"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
