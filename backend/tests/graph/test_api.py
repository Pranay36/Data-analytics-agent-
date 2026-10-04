"""The HTTP API, with the model scripted and everything else real.

Checks the contract a frontend depends on: starting a run returns at once, progress is
readable while it runs, and the finished result carries the dashboard with the
database's own figures in it.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from sqlalchemy import select

from app.db.models import Analysis
from app.db.session import get_sessionmaker
from app.llm import ChainEntry, LLMClient
from app.llm.fake import FakeLLMProvider
from app.main import create_app
from app.services import analysis_service

from .conftest import EMBEDDINGS, analysis, reply, scripted_llm
from .test_drilldown_workflow import FRAME, HEADLINE_SQL, REGION_SQL, by_region

pytestmark = pytest.mark.integration


@pytest.fixture
async def api(source, account):
    """A client for the real app, with the model replaced by a scripted one per test."""
    analysis_service.overrides["embeddings"] = EMBEDDINGS
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=account.headers
    ) as client:
        client.source_id = str(source)  # type: ignore[attr-defined]
        client.account = account  # type: ignore[attr-defined]
        yield client
    analysis_service.overrides.update(llm=None, embeddings=None)


def script(*replies) -> FakeLLMProvider:
    client, provider = scripted_llm(*replies)
    analysis_service.overrides["llm"] = client
    return provider


def headline() -> str:
    return reply(sql=HEADLINE_SQL, question_type="root_cause", frame=FRAME)


async def start(api, question="Why did revenue fall in June 2026?") -> str:
    response = await api.post(
        "/api/v1/analyses", json={"datasource_id": api.source_id, "question": question}
    )
    assert response.status_code == 202, response.text
    return response.json()["id"]


async def wait_for(api, analysis_id: str, timeout: float = 30) -> dict:
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        body = (await api.get(f"/api/v1/analyses/{analysis_id}")).json()
        if body["status"] in ("completed", "failed"):
            return body
        assert asyncio.get_event_loop().time() < deadline, f"still {body['status']}"
        await asyncio.sleep(0.1)


# ── Starting a run ───────────────────────────────────────────────────────────
async def test_starting_a_run_returns_immediately_with_an_id(api) -> None:
    script(headline(), by_region(), reply(sql=REGION_SQL), analysis())
    response = await api.post(
        "/api/v1/analyses",
        json={"datasource_id": api.source_id, "question": "Why did revenue fall in June 2026?"},
    )

    assert response.status_code == 202
    assert response.json()["status"] in ("queued", "running")
    await wait_for(api, response.json()["id"])


async def test_an_unknown_data_source_is_a_404(api) -> None:
    response = await api.post(
        "/api/v1/analyses",
        json={"datasource_id": "00000000-0000-0000-0000-000000000000", "question": "revenue?"},
    )
    assert response.status_code == 404


@pytest.mark.parametrize("body", [{}, {"question": "x"}, {"datasource_id": "nope", "question": "ok?"}])
async def test_bad_requests_are_rejected(api, body) -> None:
    assert (await api.post("/api/v1/analyses", json=body)).status_code == 422


async def test_a_question_that_is_too_short_is_rejected(api) -> None:
    response = await api.post(
        "/api/v1/analyses", json={"datasource_id": api.source_id, "question": "hi"}
    )
    assert response.status_code == 422


# ── Progress ─────────────────────────────────────────────────────────────────
class GatedProvider(FakeLLMProvider):
    """Holds the first model call until released, so a run can be observed mid-flight."""

    def __init__(self, script_, gate: asyncio.Event) -> None:
        super().__init__(script_)
        self.gate = gate

    async def complete(self, request):
        await self.gate.wait()
        return await super().complete(request)


async def test_progress_is_readable_while_the_run_is_in_flight(api) -> None:
    """The whole reason for polling: a refresh mid-run still shows where it is."""
    gate = asyncio.Event()
    provider = GatedProvider([headline(), by_region(), reply(sql=REGION_SQL), analysis()], gate)
    analysis_service.overrides["llm"] = LLMClient(
        {"fake": provider}, [ChainEntry(provider="fake", model="m")], max_retries=0
    )

    analysis_id = await start(api)
    for _ in range(100):  # let the background task reach the model call
        body = (await api.get(f"/api/v1/analyses/{analysis_id}")).json()
        if body["stage"] == "generating_sql":
            break
        await asyncio.sleep(0.05)

    assert body["status"] == "running" and body["stage"] == "generating_sql"
    assert body["dashboard"] is None and body["steps"] == []

    gate.set()
    done = await wait_for(api, analysis_id)
    assert done["status"] == "completed"


# ── The finished result ──────────────────────────────────────────────────────
async def test_a_finished_run_carries_its_dashboard_with_the_databases_figures(api) -> None:
    script(headline(), by_region(), reply(sql=REGION_SQL), analysis(summary="South dominates."))
    result = await wait_for(api, await start(api))

    assert result["status"] == "completed" and result["stop_reason"] == "answered"
    dashboard = result["dashboard"]
    current = next(k for k in dashboard["kpis"] if k["value_column"] == "current_value")

    assert current["value"] == pytest.approx(19_191_285.85)
    assert current["delta_pct"] == pytest.approx(-11.42, abs=0.01)
    assert dashboard["generated_by"] == "fallback"
    assert dashboard["spec"]["charts"], "the breakdown is charted"
    assert all(d["sql"] for d in dashboard["datasets"].values())


async def test_the_steps_taken_are_returned_including_rejected_attempts(api) -> None:
    script(
        reply(sql="SELECT 1; DROP TABLE orders"),   # rejected by the guard
        headline(), analysis(),
    )
    result = await wait_for(api, await start(api, "What was revenue in May?"))

    statuses = [(s["attempt"], s["status"]) for s in result["steps"]]
    assert statuses == [(1, "rejected"), (2, "succeeded")]
    assert result["steps"][0]["error"] and result["steps"][0]["sql"] is None


async def test_the_retrieved_context_and_stats_are_exposed_for_inspection(api) -> None:
    script(headline(), analysis())
    result = await wait_for(api, await start(api, "What was revenue?"))

    assert result["retrieved_context"]["chunks"]
    assert result["stats"]["llm_calls"] == 2 and result["stats"]["latency_ms"] > 0
    assert result["tokens"] > 0


async def test_a_refusal_is_a_completed_run_that_explains_itself(api) -> None:
    script(reply(can_answer=False, cannot_answer_reason="No influencer data exists.", sql=None))
    result = await wait_for(api, await start(api, "What is our influencer ROI?"))

    assert result["status"] == "completed" and result["stop_reason"] == "cannot_answer"
    assert result["dashboard"] is None
    assert result["error"] == {"code": "CANNOT_ANSWER", "message": "No influencer data exists."}


async def test_a_failed_run_reports_a_code_and_a_message(api) -> None:
    bad = reply(sql="SELECT o.nope FROM orders o WHERE o.nada = 1")
    script(bad, bad, bad)
    result = await wait_for(api, await start(api, "What was revenue?"))

    assert result["status"] == "failed"
    assert result["error"]["code"] == "INVALID_SQL" and result["error"]["message"]


async def test_an_unknown_run_is_a_404(api) -> None:
    missing = await api.get("/api/v1/analyses/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404
    assert (await api.get("/api/v1/analyses/not-a-uuid")).status_code == 422


# ── History ──────────────────────────────────────────────────────────────────
async def test_history_lists_runs_newest_first_with_the_source_name(api) -> None:
    script(headline(), analysis(), headline(), analysis())
    first = await start(api, "What was revenue in May 2026?")
    await wait_for(api, first)
    second = await start(api, "What was revenue in June 2026?")
    await wait_for(api, second)

    rows = (await api.get("/api/v1/analyses")).json()
    ids = [r["id"] for r in rows]

    assert ids.index(second) < ids.index(first), "newest first"
    row = next(r for r in rows if r["id"] == first)
    assert row["datasource_name"] == "graph-test" and row["status"] == "completed"
    assert "steps" not in row, "the list stays light; detail is a separate call"


async def test_history_can_be_filtered_and_paged(api) -> None:
    script(headline(), analysis())
    await wait_for(api, await start(api, "What was revenue?"))

    mine = (await api.get("/api/v1/analyses", params={"datasource_id": api.source_id})).json()
    assert mine and all(r["datasource_id"] == api.source_id for r in mine)
    assert len((await api.get("/api/v1/analyses", params={"limit": 1})).json()) == 1
    assert (await api.get("/api/v1/analyses", params={"limit": 0})).status_code == 422


# ── Interrupted runs ─────────────────────────────────────────────────────────
async def test_a_run_interrupted_by_a_restart_is_marked_failed(source, account) -> None:
    """Otherwise it would sit at "running" for ever and a polling client would wait on it."""
    async with get_sessionmaker()() as session:
        stuck = Analysis(
            user_id=account.id, data_source_id=source, question="stuck?",
            status="running", stage="analyzing",
        )
        done = Analysis(
            user_id=account.id, data_source_id=source, question="done?", status="completed"
        )
        session.add_all([stuck, done])
        await session.commit()
        stuck_id, done_id = stuck.id, done.id

    assert await analysis_service.reconcile_orphans() >= 1

    async with get_sessionmaker()() as session:
        stuck = await session.scalar(select(Analysis).where(Analysis.id == stuck_id))
        done = await session.scalar(select(Analysis).where(Analysis.id == done_id))
    assert stuck.status == "failed" and stuck.error_code == "INTERRUPTED"
    assert done.status == "completed", "finished runs are left alone"
