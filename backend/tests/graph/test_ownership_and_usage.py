"""One account cannot see another's work, and usage is attributed and capped per account."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.core.logging import analysis_context, get_user_id
from app.db.models import Analysis, DataSource, LlmCall
from app.db.session import get_sessionmaker
from app.llm.types import Usage
from app.main import create_app
from app.observability.recorder import CallRecord, DbLlmCallRecorder
from app.services import usage_service

pytestmark = pytest.mark.integration


def http(account) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()),
        base_url="http://test",
        headers=account.headers,
    )


async def add_analysis(user_id, source_id, question="private question") -> uuid.UUID:
    async with get_sessionmaker()() as session:
        row = Analysis(
            user_id=user_id, data_source_id=source_id, question=question, status="completed"
        )
        session.add(row)
        await session.commit()
        return row.id


@pytest.fixture
async def private_source(make_account):
    """A data source owned by one account, removed afterwards."""
    owner = await make_account()
    async with get_sessionmaker()() as session:
        row = DataSource(
            name=f"private-{uuid.uuid4().hex[:8]}", type="csv", config={"path": "/nowhere"},
            status="connected", owner_id=owner.id,
        )
        session.add(row)
        await session.commit()
        source_id = row.id
    yield owner, source_id
    async with get_sessionmaker()() as session:
        row = await session.get(DataSource, source_id)
        if row is not None:
            await session.delete(row)
            await session.commit()


# ── Analyses ─────────────────────────────────────────────────────────────────
async def test_an_account_reads_its_own_analysis_but_not_anyone_elses(
    make_account, source
) -> None:
    alice, bob = await make_account(), await make_account()
    analysis_id = await add_analysis(alice.id, source)

    async with http(alice) as a, http(bob) as b:
        assert (await a.get(f"/api/v1/analyses/{analysis_id}")).status_code == 200
        theirs = await b.get(f"/api/v1/analyses/{analysis_id}")

    # 404, not 403: a 403 would confirm that the analysis exists.
    assert theirs.status_code == 404


async def test_the_history_list_only_ever_holds_your_own_runs(make_account, source) -> None:
    alice, bob = await make_account(), await make_account()
    mine = await add_analysis(alice.id, source, "alice asked this")
    await add_analysis(bob.id, source, "bob asked this")

    async with http(alice) as a:
        rows = (await a.get("/api/v1/analyses")).json()

    assert [row["id"] for row in rows] == [str(mine)]


# ── Data sources ─────────────────────────────────────────────────────────────
async def test_a_private_source_is_invisible_to_everyone_but_its_owner(
    make_account, private_source
) -> None:
    owner, source_id = private_source
    other = await make_account()
    path = f"/api/v1/datasources/{source_id}"

    async with http(owner) as a, http(other) as b:
        assert (await a.get(path)).status_code == 200
        assert str(source_id) in [s["id"] for s in (await a.get("/api/v1/datasources")).json()]

        assert (await b.get(path)).status_code == 404
        assert (await b.get(f"{path}/schema")).status_code == 404
        assert (await b.get(f"{path}/examples")).status_code == 404
        assert (await b.post(f"{path}/sync")).status_code == 404
        assert (await b.delete(path)).status_code == 404
        assert str(source_id) not in [
            s["id"] for s in (await b.get("/api/v1/datasources")).json()
        ]


async def test_you_cannot_start_an_analysis_on_a_source_you_cannot_see(
    make_account, private_source
) -> None:
    _, source_id = private_source
    other = await make_account()

    async with http(other) as b:
        response = await b.post(
            "/api/v1/analyses", json={"datasource_id": str(source_id), "question": "revenue?"}
        )
    assert response.status_code == 404


async def test_a_shared_source_is_readable_by_all_but_changeable_only_by_an_admin(
    make_account, source
) -> None:
    member, admin = await make_account(), await make_account(admin=True)
    path = f"/api/v1/datasources/{source}"

    async with http(member) as m, http(admin) as a:
        assert (await m.get(path)).status_code == 200
        assert (await m.delete(path)).status_code == 404, "a member may not delete a shared source"
        assert (await m.post(f"{path}/sync")).status_code == 404
        assert (await a.get(path)).status_code == 200


# ── Usage attribution ────────────────────────────────────────────────────────
async def test_the_user_travels_with_the_run_into_the_context() -> None:
    with analysis_context("an-analysis", "a-user"):
        assert get_user_id() == "a-user"
    assert get_user_id() is None, "the context must not leak out of the run"


async def test_a_recorded_model_call_names_who_caused_it(account) -> None:
    await DbLlmCallRecorder().record(
        CallRecord(
            user_id=str(account.id), agent="query", provider="p", model="m", success=True,
            usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
        )
    )
    async with get_sessionmaker()() as session:
        rows = (await session.scalars(select(LlmCall).where(LlmCall.user_id == account.id))).all()
    assert len(rows) == 1 and rows[0].total_tokens == 15


async def add_calls(user_id, n, *, cached=False, success=True, age=timedelta(0), tokens=10) -> None:
    async with get_sessionmaker()() as session:
        for _ in range(n):
            session.add(
                LlmCall(
                    user_id=user_id, agent="query", provider="p", model="m", success=success,
                    cached=cached, input_tokens=tokens, output_tokens=tokens,
                    created_at=datetime.now(UTC) - age,
                )
            )
        await session.commit()


async def test_usage_counts_only_todays_uncached_calls_of_this_account(make_account) -> None:
    me, other = await make_account(), await make_account()
    await add_calls(me.id, 2)
    await add_calls(me.id, 1, success=False)  # a failed call still spent a provider request
    await add_calls(me.id, 5, cached=True)  # served from disk: spent nothing
    await add_calls(me.id, 4, age=timedelta(days=2))  # not today
    await add_calls(other.id, 9)  # someone else's

    async with get_sessionmaker()() as session:
        used = await usage_service.today(session, me.id)

    assert used.llm_calls == 3
    assert used.input_tokens == 30 and used.output_tokens == 30


async def test_me_reports_usage_and_limits(account) -> None:
    await add_calls(account.id, 2)
    async with http(account) as client:
        body = (await client.get("/api/v1/auth/me")).json()

    assert body["usage_today"]["llm_calls"] == 2
    assert body["limits"]["llm_calls_per_day"] > 0
    assert body["resets_at"]


# ── Quotas ───────────────────────────────────────────────────────────────────
def ask(client, source_id):
    return client.post(
        "/api/v1/analyses", json={"datasource_id": str(source_id), "question": "revenue?"}
    )


async def test_the_daily_model_call_limit_stops_a_new_analysis(
    make_account, source, monkeypatch
) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("QUOTA_LLM_CALLS_PER_DAY", "3")
    get_settings.cache_clear()
    me = await make_account()
    await add_calls(me.id, 3)

    async with http(me) as client:
        response = await ask(client, source)

    assert response.status_code == 429
    assert "model-call limit" in response.json()["detail"]
    assert "resets at" in response.json()["detail"]


async def test_the_daily_analysis_limit_stops_a_new_analysis(
    make_account, source, monkeypatch
) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("QUOTA_ANALYSES_PER_DAY", "1")
    get_settings.cache_clear()
    me = await make_account()
    await add_analysis(me.id, source)

    async with http(me) as client:
        response = await ask(client, source)

    assert response.status_code == 429
    assert "analysis limit" in response.json()["detail"]


async def test_one_accounts_spending_does_not_block_another(make_account, monkeypatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("QUOTA_LLM_CALLS_PER_DAY", "3")
    get_settings.cache_clear()
    heavy, light = await make_account(), await make_account()
    await add_calls(heavy.id, 5)

    async with get_sessionmaker()() as session:
        assert await usage_service.over_quota(session, _user(heavy)) == "model-call"
        assert await usage_service.over_quota(session, _user(light)) is None


async def test_cached_calls_do_not_count_towards_the_limit(make_account, monkeypatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("QUOTA_LLM_CALLS_PER_DAY", "3")
    get_settings.cache_clear()
    me = await make_account()
    await add_calls(me.id, 10, cached=True)

    async with get_sessionmaker()() as session:
        assert await usage_service.over_quota(session, _user(me)) is None


def _user(account):
    from app.db.models import User

    return User(id=account.id, email=account.email, hashed_password="x")
