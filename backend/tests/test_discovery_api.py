from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.discovery.http import SafeFetcher
from app.main import app
from app.models import Company, CompanyDiscoveryEvent, DiscoveryCandidate, DiscoveryRun, Job
from app.services.companies import new_company, record_event
from app.services.queue import recover_expired_runs
from app.worker import run_worker
from tests.helpers import (
    FakeSearch,
    MockNetwork,
    empty_structured_sources,
    fast_settings,
    iso_hours_ago,
    make_explorer,
)


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


@pytest.fixture
def no_network(monkeypatch):
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("HTTP handlers must not perform remote I/O.")

    monkeypatch.setattr(SafeFetcher, "_send_once", forbidden)


async def test_runs_and_imports_are_queued_without_remote_io(client, no_network) -> None:
    run = await client.post(
        "/api/v1/discovery/runs", json={"kind": "exploration", "budgets": {"max_pages": 5}}
    )
    assert run.status_code == 202
    assert run.json()["status"] == "QUEUED" and run.json()["budgets"]["max_pages"] == 5
    again = await client.post("/api/v1/discovery/runs", json={"kind": "exploration"})
    assert again.json()["id"] == run.json()["id"]  # an active run of the same kind is reused

    imported = await client.post("/api/v1/ingestion/greenhouse", json={"board_token": "acme"})
    assert imported.status_code == 202
    assert (imported.json()["kind"], imported.json()["status"]) == ("ingestion", "QUEUED")
    assert (
        await client.post("/api/v1/ingestion/lever", json={"company_slug": "bad slug"})
    ).status_code == 422
    assert (
        await client.post(
            "/api/v1/discovery/runs", json={"kind": "exploration", "budgets": {"max_pages": -1}}
        )
    ).status_code == 422
    assert (
        await client.post("/api/v1/discovery/runs", json={"kind": "delete_everything"})
    ).status_code == 422
    fetched = await client.get(f"/api/v1/discovery/runs/{run.json()['id']}")
    assert fetched.json()["status"] == "QUEUED"


async def test_seed_import_is_normalized_and_replay_safe(client, no_network) -> None:
    first = await client.post(
        "/api/v1/companies/seeds", json={"names": ["Amazon", " amazon ", "Morgan Stanley", ""]}
    )
    assert first.status_code == 202
    names = [company["name"] for company in first.json()["companies"]]
    assert names == ["Amazon", "Morgan Stanley"]
    second = await client.post("/api/v1/companies/seeds", json={"names": ["AMAZON"], "tier": "A"})
    assert second.json()["companies"][0]["id"] == first.json()["companies"][0]["id"]
    assert second.json()["companies"][0]["tier"] == "A"
    assert (
        await client.post("/api/v1/companies/seeds", json={"names": ["   "]})
    ).status_code == 422
    assert (
        await client.post("/api/v1/companies/seeds", json={"names": ["X"], "tier": "Z"})
    ).status_code == 422
    async with SessionLocal() as session:
        assert await session.scalar(select(func.count(Company.id))) == 2
        event = await session.scalar(
            select(CompanyDiscoveryEvent)
            .where(CompanyDiscoveryEvent.reason == "MANUAL_SEED")
            .limit(1)
        )
        amazon = await session.scalar(select(Company).where(Company.name_key == "amazon"))
        seed_event = await session.scalar(
            select(CompanyDiscoveryEvent).where(CompanyDiscoveryEvent.company_id == amazon.id)
        )
        assert event is not None and seed_event.observation_count == 2
        assert amazon.is_seed and amazon.domain is None and amazon.status == "DISCOVERED"


async def test_settings_are_validated_and_persisted(client) -> None:
    current = (await client.get("/api/v1/discovery/settings")).json()
    assert current["tiers"] == ["S", "A", "B", "C", "UNCATEGORIZED"]
    assert {provider["name"] for provider in current["provider_status"]} >= {
        "duckduckgo",
        "hackernews",
        "bing",
    }
    assert not any(
        provider["requires_key"] and provider["enabled"] for provider in current["provider_status"]
    )

    bad = [
        {"company_score_weights": {"data_hiring_signal": -1}},
        {"company_score_weights": {"made_up": 1}},
        {"freshness_hours": 0},
        {"freshness_hours": 300, "secondary_freshness_hours": 100},
        {"tiers": ["A", "a"]},
        {"tiers": ["A", "B", "UNCATEGORIZED"]},  # S is required while the S-tier list is set
        {"tailoring_policy": {"A": {"mode": "min_fit"}}},  # threshold missing
        {"application_priority_thresholds": {"P0": 50, "P1": 70}},  # not descending
        {"company_registry": [{"name": "Acme", "career_urls": ["http://acme.example/jobs"]}]},
        {"unknown_field": True},
    ]
    for body in bad:
        assert (await client.patch("/api/v1/discovery/settings", json=body)).status_code == 422, (
            body
        )

    updated = await client.patch(
        "/api/v1/discovery/settings",
        json={
            "tiers": ["S", "A", "B", "C", "Watchlist"],
            "budgets": {"max_new_companies": 0, "max_search_queries": 0},
            "company_score_weights": {"data_hiring_signal": 0.5},
            "freshness_hours": 24,
            "s_tier_companies": ["Acme Data"],
        },
    )
    assert updated.status_code == 200, updated.text
    body = (await client.get("/api/v1/discovery/settings")).json()
    assert body["tiers"][-1] == "Watchlist" and body["default_tier"] == "Watchlist"
    assert body["budgets"]["max_new_companies"] == 0 and body["budgets"]["max_pages"] == 150
    assert body["company_score_weights"]["data_hiring_signal"] == 0.5
    assert body["freshness_hours"] == 24
    assert body["s_tier_companies"] == ["Acme Data"]
    assert "UNCATEGORIZED" not in body["tailoring_policy"]  # dropped with the tier
    assert body["llm_status"]["configured"] is False


async def test_company_updates_and_sources_reject_unsafe_input(client, no_network) -> None:
    seeded = await client.post("/api/v1/companies/seeds", json={"names": ["Acme"]})
    company_id = seeded.json()["companies"][0]["id"]
    for body in (
        {"careers_url": "http://127.0.0.1/careers"},
        {"careers_url": "file:///etc/passwd"},
        {"domain": "https://acme.com/path"},
        {"tier": "Platinum"},
        {"status": "MADE_UP"},
    ):
        assert (
            await client.patch(f"/api/v1/companies/{company_id}", json=body)
        ).status_code == 422, body
    for url in (
        "http://localhost/jobs",
        "https://intranet/jobs",
        "https://jobs.example.com:9000/x",
    ):
        assert (
            await client.post(f"/api/v1/companies/{company_id}/sources", json={"url": url})
        ).status_code == 422

    updated = await client.patch(
        f"/api/v1/companies/{company_id}",
        json={"domain": "www.Acme.com", "careers_url": "https://acme.com/careers", "tier": "B"},
    )
    assert updated.status_code == 200
    assert (updated.json()["domain"], updated.json()["tier"]) == ("acme.com", "B")
    source = await client.post(
        f"/api/v1/companies/{company_id}/sources",
        json={"url": "https://boards.greenhouse.io/acme/jobs/1"},
    )
    assert source.status_code == 201
    assert (source.json()["platform"], source.json()["url"]) == (
        "greenhouse",
        "https://boards.greenhouse.io/acme",
    )
    detail = (await client.get(f"/api/v1/companies/{company_id}")).json()
    assert {item["platform"] for item in detail["sources"]} == {"generic_html", "greenhouse"}
    # Timestamps are explicitly UTC so clients never read them as local time.
    assert detail["created_at"].endswith(("Z", "+00:00"))
    assert detail["sources"][0]["created_at"].endswith(("Z", "+00:00"))
    scan = await client.post(f"/api/v1/companies/{company_id}/scan")
    assert scan.status_code == 202 and scan.json()["kind"] == "company_scan"
    # Adding the source already queued this company's scan; the request reuses it.
    assert scan.json()["id"] == source.json()["scan_run_id"]
    assert scan.json()["created_at"].endswith(("Z", "+00:00"))


async def test_company_rename_keeps_alias_and_rejects_conflicts(client, no_network) -> None:
    seeded = await client.post(
        "/api/v1/companies/seeds", json={"names": ["acme.wd1.myworkdayjobs", "Globex"]}
    )
    acme_id, _globex_id = (company["id"] for company in seeded.json()["companies"])
    assert (
        await client.patch(f"/api/v1/companies/{acme_id}", json={"name": "Globex"})
    ).status_code == 409
    assert (
        await client.patch(f"/api/v1/companies/{acme_id}", json={"name": "!!!"})
    ).status_code == 422
    renamed = await client.patch(f"/api/v1/companies/{acme_id}", json={"name": "Acme Robotics"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Acme Robotics"
    assert "acme.wd1.myworkdayjobs" in renamed.json()["aliases"]
    listed = (await client.get("/api/v1/companies", params={"q": "Acme Robotics"})).json()
    assert [item["name"] for item in listed["items"]] == ["Acme Robotics"]


async def make_discovered(name: str) -> tuple[str, str]:
    async with SessionLocal() as session:
        company = new_company(
            name,
            tier="UNCATEGORIZED",
            status="DISCOVERED",
            review_status="PENDING",
            discovery_source="fakesearch",
        )
        session.add(company)
        await session.flush()
        event = await record_event(
            session,
            company,
            reason="JOB_SEARCH",
            source="fakesearch",
            source_url=f"https://boards.greenhouse.io/{name.lower()}/jobs/1",
            evidence="Job Application for Data Engineer",
            confidence=0.8,
            evidence_data={"role_relevance": 0.95},
        )
        session.add(
            DiscoveryCandidate(
                company_id=company.id, reason="test", priority=10, priority_breakdown={}, depth=1
            )
        )
        await session.commit()
        return str(company.id), str(event.id)


async def test_feed_review_accepts_or_ignores_with_explicit_state(client) -> None:
    accepted_company, accepted_event = await make_discovered("Accepto")
    ignored_company, ignored_event = await make_discovered("Ignoro")
    feed = (await client.get("/api/v1/discovery/feed")).json()
    assert {item["status"] for item in feed["items"]} == {"PENDING"}

    accepted = await client.post(
        f"/api/v1/discovery/feed/{accepted_event}/review", json={"action": "accept"}
    )
    assert accepted.json()["status"] == "ACCEPTED"
    ignored = await client.post(
        f"/api/v1/discovery/feed/{ignored_event}/review", json={"action": "ignore"}
    )
    assert ignored.json()["status"] == "IGNORED"
    assert (
        await client.post(
            f"/api/v1/discovery/feed/{ignored_event}/review", json={"action": "accept"}
        )
    ).status_code == 409
    assert (
        await client.post(
            f"/api/v1/discovery/feed/{accepted_event}/review", json={"action": "maybe"}
        )
    ).status_code == 422

    async with SessionLocal() as session:
        kept = await session.get(Company, __import__("uuid").UUID(accepted_company))
        dropped = await session.get(Company, __import__("uuid").UUID(ignored_company))
        assert kept.review_status == "ACCEPTED"
        assert (dropped.review_status, dropped.status) == ("IGNORED", "INACTIVE")
        skipped = await session.scalar(
            select(DiscoveryCandidate.status).where(DiscoveryCandidate.company_id == dropped.id)
        )
        assert skipped == "SKIPPED"
    frontier = (await client.get("/api/v1/discovery/frontier", params={"status": "PENDING"})).json()
    assert [item["company_name"] for item in frontier["items"]] == ["Accepto"]


async def test_zero_budgets_bound_exploration_completely(client) -> None:
    settings = fast_settings()
    network = MockNetwork()
    empty_structured_sources(network)
    search = FakeSearch(
        {
            "": [
                (
                    "Job Application for Data Engineer at Acme",
                    "https://boards.greenhouse.io/acme/jobs/1",
                    "",
                )
            ]
        }
    )
    await client.post(
        "/api/v1/discovery/runs",
        json={
            "kind": "exploration",
            "budgets": {"max_search_queries": 0, "max_pages": 0, "max_new_companies": 0},
        },
    )
    await run_worker(
        once=True,
        explorer=make_explorer(settings, network, search, SessionLocal),
        session_factory=SessionLocal,
        settings=settings,
    )
    async with SessionLocal() as session:
        run = await session.scalar(select(DiscoveryRun))
        assert run.status == "COMPLETED", run.errors
        assert run.progress["used"].get("max_search_queries", 0) == 0
        assert "max_pages" in run.progress["budget_exhausted"]
        assert await session.scalar(select(func.count(Company.id))) == 0
    assert search.queries == [] and network.requests == []


async def test_expired_leases_are_retried_then_failed() -> None:
    async with SessionLocal() as session:
        expired = datetime.now(UTC) - timedelta(minutes=1)
        retry = DiscoveryRun(
            kind="scan",
            status="RUNNING",
            attempts=1,
            max_attempts=3,
            lease_owner="dead",
            lease_expires_at=expired,
            params={},
            budgets={},
            progress={},
            errors=[],
        )
        final = DiscoveryRun(
            kind="scan",
            status="RUNNING",
            attempts=3,
            max_attempts=3,
            lease_owner="dead",
            lease_expires_at=expired,
            params={},
            budgets={},
            progress={},
            errors=[],
        )
        session.add_all([retry, final])
        await session.commit()
        retry_id, final_id = retry.id, final.id
    assert await recover_expired_runs(SessionLocal) == 2
    async with SessionLocal() as session:
        assert (await session.get(DiscoveryRun, retry_id)).status == "QUEUED"
        failed = await session.get(DiscoveryRun, final_id)
        assert failed.status == "FAILED" and "lease expired" in failed.error


async def test_worker_runs_legacy_board_imports_and_reports_missing_boards(client) -> None:
    settings = fast_settings()
    network = MockNetwork()
    network.add(
        "boards-api.greenhouse.io",
        "/v1/boards/acme/jobs",
        {
            "jobs": [
                {
                    "id": 1,
                    "title": "Data Engineer",
                    "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
                    "location": {"name": "Pune, India"},
                    "first_published": iso_hours_ago(3),
                    "updated_at": iso_hours_ago(1),
                    "content": "Spark pipelines",
                },
                {
                    "id": 2,
                    "title": "Account Executive",
                    "absolute_url": "https://boards.greenhouse.io/acme/jobs/2",
                    "location": {"name": "Pune, India"},
                    "first_published": iso_hours_ago(3),
                    "updated_at": iso_hours_ago(1),
                    "content": "Sell software",
                },
            ]
        },
    )
    network.add("boards-api.greenhouse.io", "/v1/boards/acme", {"name": "Acme Corp"})
    await client.post("/api/v1/ingestion/greenhouse", json={"board_token": "acme"})
    await client.post("/api/v1/ingestion/greenhouse", json={"board_token": "missing"})
    explorer = make_explorer(settings, network, FakeSearch(), SessionLocal)
    assert (
        await run_worker(
            once=True, explorer=explorer, session_factory=SessionLocal, settings=settings
        )
        == 2
    )
    async with SessionLocal() as session:
        runs = {
            run.params["identifier"]: run
            for run in (await session.scalars(select(DiscoveryRun))).all()
        }
        assert runs["acme"].status == "COMPLETED"
        assert runs["acme"].progress["ingestion"]["created"] == 2  # direct imports keep every role
        assert runs["missing"].status == "FAILED"
        assert "was not found" in runs["missing"].error
        company = await session.scalar(select(Company).where(Company.name == "Acme Corp"))
        assert company.review_status == "ACCEPTED" and company.status == "ACTIVE"
        assert (
            await session.scalar(select(func.count(Job.id)).where(Job.company_id == company.id))
            == 2
        )
