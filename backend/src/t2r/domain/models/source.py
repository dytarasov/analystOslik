from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class DataSourceCredentials(BaseModel):
    host: str
    port: int
    database: str
    username: str
    password: str
    secure: bool = False
    extra_settings: dict[str, Any] = Field(default_factory=dict)


class DataSourceCreate(BaseModel):
    name: str
    kind: str = "clickhouse"
    host: str
    port: int = 8123
    database: str
    username: str
    password: str
    secure: bool = False
    extra_settings: dict[str, Any] = Field(default_factory=dict)


class DataSourceUpdate(BaseModel):
    name: str | None = None
    host: str | None = None
    port: int | None = None
    database: str | None = None
    username: str | None = None
    password: str | None = None
    secure: bool | None = None
    extra_settings: dict[str, Any] | None = None
    # Provided (even as "") → glossary is set. Omitted/None → left unchanged.
    glossary_md: str | None = None
    # Same semantics as glossary_md: typical SQL recipes, kept separate so they
    # don't bloat the prompt-injected glossary.
    sql_notes_md: str | None = None


class DataSource(BaseModel):
    id: UUID
    name: str
    kind: str
    host: str
    port: int
    database: str
    username: str
    secure: bool
    extra_settings: dict[str, Any]
    readonly_verified: bool
    last_test_at: datetime | None = None
    last_test_status: str | None = None
    last_test_error: str | None = None
    last_profiling_run_id: UUID | None = None
    last_profiled_at: datetime | None = None
    profiling_status: str = "never_profiled"
    glossary_md: str | None = None
    glossary_ingested_at: datetime | None = None
    sql_notes_md: str | None = None
    sql_notes_ingested_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class GlossaryIngestResult(BaseModel):
    ok: bool
    notes: int = 0
    metrics: int = 0
    terms: int = 0
    columns: int = 0
    relations: int = 0
    warnings: list[str] = Field(default_factory=list)


class SqlNotesIngestResult(BaseModel):
    ok: bool
    recipes: int = 0
    warnings: list[str] = Field(default_factory=list)


class TestConnectionResult(BaseModel):
    ok: bool
    version: str | None = None
    readonly: bool = False
    error: str | None = None
