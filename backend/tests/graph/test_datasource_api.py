"""Creating data sources over HTTP: CSV upload, indexing, examples."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from app.db.models import DataSource, KnowledgeChunk
from app.db.models.knowledge import EMBEDDING_DIM
from app.db.session import get_sessionmaker
from app.main import create_app
from app.rag import FakeEmbeddingProvider
from app.services import datasource_service as service

pytestmark = pytest.mark.integration

SALES = b"region,amount,placed_on\nNorth,100.5,2026-01-02\nSouth,250,2026-01-03\nNorth,75,2026-02-01\n"


@pytest.fixture
async def api(source):
    service.embedding_override["provider"] = FakeEmbeddingProvider(EMBEDDING_DIM)
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        client.source_id = str(source)  # type: ignore[attr-defined]
        yield client
    service.embedding_override["provider"] = None

    async with get_sessionmaker()() as session:  # remove anything the test uploaded
        for row in (await session.scalars(select(DataSource).where(DataSource.name.like("csv-test%")))):
            await session.delete(row)
        await session.commit()


async def upload(api, name="csv-test", files=None):
    return await api.post(
        "/api/v1/datasources/csv", data={"name": name},
        files=files or [("files", ("Q1 Sales.csv", SALES, "text/csv"))],
    )


async def test_an_uploaded_csv_becomes_a_queryable_source_with_its_schema(api) -> None:
    response = await upload(api)
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["type"] == "csv" and body["status"] == "connected" and body["table_count"] == 1
    schema = (await api.get(f"/api/v1/datasources/{body['id']}/schema")).json()
    table = schema["tables"][0]
    assert table["table_name"] == "q1_sales", "the file name becomes a usable table name"
    assert {c["name"] for c in table["columns"]} == {"region", "amount", "placed_on"}
    assert next(c for c in table["columns"] if c["name"] == "region")["sample_values"] == ["North", "South"]


async def test_an_uploaded_source_is_indexed_so_retrieval_has_something_to_search(api) -> None:
    body = (await upload(api)).json()

    async with get_sessionmaker()() as session:
        chunks = (await session.scalars(
            select(KnowledgeChunk).where(KnowledgeChunk.data_source_id == body["id"]))).all()
    assert [c.kind for c in chunks] == ["table"]
    assert "region" in chunks[0].content and chunks[0].embedding is not None


async def test_the_response_never_contains_a_filesystem_path_secret(api) -> None:
    """A CSV source has no password, but its server-side path is not for clients either."""
    body = (await upload(api)).json()
    assert body["has_secret"] is False


@pytest.mark.parametrize(
    ("files", "reason"),
    [
        ([("files", ("notes.txt", b"hello", "text/plain"))], "not a .csv"),
        ([("files", ("empty.csv", b"a,b\n", "text/csv"))], "no rows"),
        ([("files", ("ragged.csv", b"a,b,c\n1,2\n4,5,6\n", "text/csv"))], "inconsistent"),
    ],
)
async def test_bad_uploads_are_rejected_with_a_reason(api, files, reason) -> None:
    response = await upload(api, files=files)
    assert response.status_code == 422
    assert reason in response.json()["detail"]


async def test_a_rejected_upload_leaves_nothing_behind(api) -> None:
    await upload(api, files=[("files", ("ragged.csv", b"a,b,c\n1,2\n4,5,6\n", "text/csv"))])
    listed = (await api.get("/api/v1/datasources")).json()
    assert not any(s["name"] == "csv-test" for s in listed)


async def test_a_duplicate_name_is_a_conflict(api) -> None:
    assert (await upload(api)).status_code == 201
    assert (await upload(api)).status_code == 409


async def test_a_path_in_the_filename_cannot_choose_where_it_is_written(api) -> None:
    """Only the base name is used, so a hostile client cannot write outside its folder."""
    response = await upload(api, files=[("files", ("../../etc/evil.csv", SALES, "text/csv"))])
    assert response.status_code == 201
    schema = (await api.get(f"/api/v1/datasources/{response.json()['id']}/schema")).json()
    assert schema["tables"][0]["table_name"] == "evil"


async def test_several_files_become_several_tables(api) -> None:
    files = [("files", ("a.csv", SALES, "text/csv")), ("files", ("b.csv", SALES, "text/csv"))]
    body = (await upload(api, files=files)).json()
    assert body["table_count"] == 2


async def test_example_questions_come_from_the_sources_verified_queries(api) -> None:
    async with get_sessionmaker()() as session:
        session.add(KnowledgeChunk(
            data_source_id=api.source_id, kind="example_query", title="What was revenue?",
            content="x", content_hash="h", payload={}, source="seed",
        ))
        await session.commit()

    assert (await api.get(f"/api/v1/datasources/{api.source_id}/examples")).json() == ["What was revenue?"]
    missing = await api.get("/api/v1/datasources/00000000-0000-0000-0000-000000000000/examples")
    assert missing.status_code == 404
