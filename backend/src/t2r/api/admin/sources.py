from uuid import UUID

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter

from t2r.api.deps import AdminDep
from t2r.domain.models.source import (
    DataSource,
    DataSourceCreate,
    DataSourceUpdate,
    GlossaryIngestResult,
    TestConnectionResult,
)
from t2r.services.glossary_service import GlossaryService
from t2r.services.source_service import SourceService

router = APIRouter(prefix="/api/admin/sources", tags=["admin-sources"], dependencies=[AdminDep])


@router.get("", response_model=list[DataSource])
@inject
async def list_sources(svc: FromDishka[SourceService]) -> list[DataSource]:
    return await svc.list()


@router.post("", response_model=DataSource)
@inject
async def create_source(
    payload: DataSourceCreate, svc: FromDishka[SourceService]
) -> DataSource:
    return await svc.create(payload)


@router.get("/{source_id}", response_model=DataSource)
@inject
async def get_source(source_id: UUID, svc: FromDishka[SourceService]) -> DataSource:
    return await svc.get(source_id)


@router.patch("/{source_id}", response_model=DataSource)
@inject
async def update_source(
    source_id: UUID,
    payload: DataSourceUpdate,
    svc: FromDishka[SourceService],
) -> DataSource:
    return await svc.update(source_id, payload)


@router.delete("/{source_id}", status_code=204)
@inject
async def delete_source(source_id: UUID, svc: FromDishka[SourceService]) -> None:
    await svc.delete(source_id)


@router.post("/{source_id}/glossary/ingest", response_model=GlossaryIngestResult)
@inject
async def ingest_glossary(
    source_id: UUID, svc: FromDishka[GlossaryService]
) -> GlossaryIngestResult:
    """Decompose the source's glossary into the semantic layer (terms, metrics,
    embedded notes, relations) for on-demand retrieval by the agent."""
    return await svc.ingest(source_id)


@router.post("/{source_id}/test-connection", response_model=TestConnectionResult)
@inject
async def test_connection(
    source_id: UUID, svc: FromDishka[SourceService]
) -> TestConnectionResult:
    return await svc.test_connection(source_id)


@router.post("/test-credentials", response_model=TestConnectionResult)
@inject
async def test_credentials(
    payload: DataSourceCreate, svc: FromDishka[SourceService]
) -> TestConnectionResult:
    return await svc.test_credentials(payload)
