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
    # Структурные JSON-логи (для прода/агрегаторов). None → JSON в prod, цветной
    # консольный вывод в dev. Можно форсировать через T2R_JSON_LOGS.
    json_logs: bool | None = None
    # Secure-флаг на cookie. None → secure в prod, открыто в dev. При деплое за
    # HTTPS reverse-proxy оставляйте дефолт (T2R_ENV=prod).
    cookie_secure: bool | None = None

    admin_login: str
    admin_password_hash: str
    jwt_secret: str
    jwt_ttl_seconds: int = 86400

    # UUID-ключ доступа к клиентской части. Пусто → гейт выключен (удобно в dev).
    # В проде задайте случайный UUID: python -c "import uuid; print(uuid.uuid4())".
    access_key: str = ""

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
    # Glossary / SQL-notes ingest emits a larger JSON payload than a normal call;
    # the default 4096 truncated it on big inputs → invalid JSON. Combined with
    # per-section chunking, this keeps each chunk's output well within budget.
    llm_ingest_max_tokens: int = 8192
    # Per-request HTTP timeout (seconds) and bounded retries for the LLM client.
    # Без них AsyncOpenAI берёт дефолт 600с × 2 ретрая → зависший апстрим держит
    # UI в спиннере минутами. 60с × (1+1) ограничивает один вызов ~2 минутами.
    llm_request_timeout: float = 60.0
    llm_max_retries: int = 1
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
    emb_request_timeout: float = 30.0

    ch_default_max_execution_time: int = 30
    ch_default_limit: int = 10000

    # Жёсткий потолок wall-clock на один прогон ReAct-агента (сек). По истечении
    # цикл завершается последним полученным результатом, а не висит бесконечно.
    agent_run_budget_seconds: int = 150
    # Сколько ждём ответа пользователя на confirm_plan/ask_user (сек). По таймауту
    # прогон корректно завершается, освобождая слот сессии и соединение БД.
    client_answer_timeout_seconds: int = 300

    sse_ping_interval: int = 15

    client_rate_limit: str = "10/minute"
    # Лимит попыток ввода ключа доступа и логина админа (защита от перебора).
    access_rate_limit: str = "10/minute"
    admin_login_rate_limit: str = "5/minute"

    export_dir: str = "/var/t2r/exports"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_prod(self) -> bool:
        return self.env.lower() == "prod"

    @property
    def access_required(self) -> bool:
        return bool(self.access_key.strip())

    @property
    def cookie_secure_effective(self) -> bool:
        return self.cookie_secure if self.cookie_secure is not None else self.is_prod

    @property
    def json_logs_effective(self) -> bool:
        return self.json_logs if self.json_logs is not None else self.is_prod


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
