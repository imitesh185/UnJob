import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.config import Settings
from app.connectors.greenhouse import greenhouse_job
from app.db import SessionLocal
from app.discovery.jsonld import freshness_status, parse_datetime
from app.models import CareerSource, Job, JobSource
from app.schemas import IngestedJob, JobRead
from app.services.companies import new_company
from app.services.ingestion import DiscoveryService

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def test_freshness_requires_a_known_posting_date_and_is_conservative() -> None:
    def status(posted, precision):
        return freshness_status(posted, precision, NOW, 48, 168)

    assert status(NOW - timedelta(hours=10), "datetime") == "fresh"
    assert status(NOW - timedelta(hours=47.5), "datetime") == "fresh"
    assert status(NOW - timedelta(hours=50), "datetime") == "recent"
    assert status(NOW - timedelta(hours=200), "datetime") == "stale"
    assert status(None, None) == "unknown"
    today, precision = parse_datetime("2026-10-07")
    assert precision == "date" and status(today, precision) == "fresh"
    # A date-only posting from two days ago may be older than 48 hours, so it is not claimed fresh.
    two_days, _ = parse_datetime("2026-10-05")
    assert status(two_days, "date") == "recent"


def test_dates_keep_their_source_precision() -> None:
    assert parse_datetime("2026-10-07T07:06:09+00:00")[1] == "datetime"
    assert parse_datetime("2026-10-05T05:15:43")[1] == "local_datetime"
    assert parse_datetime("October  6, 2026") == (datetime(2026, 10, 6, tzinfo=UTC), "date")
    assert parse_datetime("2024-05-01 10:00:00 UTC")[1] == "datetime"
    assert parse_datetime("not a date") == (None, None)


def test_api_timestamps_are_utc_but_zoneless_posting_times_stay_zoneless() -> None:
    # SQLite returns stored UTC timestamps without a zone.
    base = {
        "id": uuid.uuid4(),
        "company": "Acme",
        "title": "Data Engineer",
        "location": "Remote",
        "remote_status": "remote",
        "salary": None,
        "employment_type": None,
        "ats": "greenhouse",
        "discovered_at": datetime(2026, 10, 7, 10, 0),
        "description": "",
        "requirements": [],
        "preferred_requirements": [],
        "technology_stack": [],
        "experience_requirement": None,
        "application_url": "https://boards.greenhouse.io/acme/jobs/1",
        "status": "NEW",
        "overall_score": 50.0,
        "score_breakdown": {},
        "score_reasons": [],
        "sources": [],
    }
    local = JobRead.model_validate(
        {**base, "posted_at": datetime(2026, 10, 7, 9), "posted_at_precision": "local_datetime"}
    )
    dated = JobRead.model_validate(
        {**base, "posted_at": datetime(2026, 10, 7), "posted_at_precision": "date"}
    )
    assert local.discovered_at.tzinfo is UTC and local.posted_at.tzinfo is None
    assert dated.posted_at == datetime(2026, 10, 7, tzinfo=UTC)
    assert local.model_dump(mode="json")["discovered_at"].endswith(("Z", "+00:00"))


def test_greenhouse_update_time_is_not_used_as_posting_time() -> None:
    item = {
        "id": 1,
        "title": "Data Engineer",
        "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
        "location": {"name": "Remote - India"},
        "updated_at": "2026-10-07T10:00:00-04:00",
        "content": "&lt;p&gt;Spark &amp;amp; Kafka&lt;/p&gt;",
    }
    unpublished = greenhouse_job(item, "Acme")
    assert unpublished.posted_at is None
    assert unpublished.source_updated_at is not None
    assert unpublished.description == "Spark & Kafka"
    assert unpublished.remote_status == "remote"
    published = greenhouse_job({**item, "first_published": "2026-09-01T09:00:00-04:00"}, "Acme")
    assert published.posted_at == datetime(2026, 9, 1, 13, 0, tzinfo=UTC)
    assert published.posted_at_precision == "datetime"


def job(external_id: str, title: str = "Data Engineer", hours_ago: float | None = 2) -> IngestedJob:
    return IngestedJob(
        company="Acme",
        title=title,
        location="Bengaluru, India",
        application_url=f"https://boards.greenhouse.io/acme/jobs/{external_id}",
        source_url=f"https://boards.greenhouse.io/acme/jobs/{external_id}",
        source="greenhouse",
        ats="greenhouse",
        external_id=external_id,
        posted_at=None if hours_ago is None else datetime.now(UTC) - timedelta(hours=hours_ago),
        description=f"{title}: build pipelines with Spark and Kafka. Unique requirement {external_id}.",
        raw_payload={"id": external_id},
    )


async def setup_source():
    async with SessionLocal() as session:
        company = new_company("Acme", tier="B", status="ACTIVE", review_status="ACCEPTED")
        session.add(company)
        await session.flush()
        source = CareerSource(
            company_id=company.id,
            url="https://boards.greenhouse.io/acme",
            platform="greenhouse",
            platform_identifier={"token": "acme"},
        )
        session.add(source)
        await session.commit()
        return company.id, source.id


async def ingest(jobs, company_id, source_id, *, complete: bool):
    async with SessionLocal() as session:
        from app.models import Company

        company = await session.get(Company, company_id)
        source = await session.get(CareerSource, source_id)
        return await DiscoveryService(Settings()).ingest_jobs(
            session,
            jobs,
            company=company,
            career_source=source,
            complete=complete,
            relevant_only=True,
        )


async def test_rescans_refresh_jobs_and_only_complete_scans_close_them() -> None:
    company_id, source_id = await setup_source()
    first = await ingest(
        [job("1"), job("2", "Data Platform Engineer"), job("3", "Data Analyst")],
        company_id,
        source_id,
        complete=True,
    )
    assert (first.created, first.irrelevant_skipped) == (2, 1)

    partial = await ingest([job("1")], company_id, source_id, complete=False)
    assert partial.closed == 0

    renamed = job("1", "Senior Data Engineer")
    complete = await ingest([renamed], company_id, source_id, complete=True)
    assert (complete.updated, complete.closed) == (1, 1)
    async with SessionLocal() as session:
        jobs = {item.title: item for item in (await session.scalars(select(Job))).all()}
        assert jobs["Senior Data Engineer"].listing_status == "OPEN"
        assert jobs["Senior Data Engineer"].company_id == company_id
        assert jobs["Data Platform Engineer"].listing_status == "CLOSED"
        closed_source = await session.scalar(select(JobSource).where(JobSource.external_id == "2"))
        assert closed_source.listing_status == "CLOSED"

    reopened = await ingest(
        [job("2", "Data Platform Engineer")], company_id, source_id, complete=False
    )
    assert reopened.updated == 1
    async with SessionLocal() as session:
        platform = await session.scalar(select(Job).where(Job.title == "Data Platform Engineer"))
        assert platform.listing_status == "OPEN" and platform.closed_at is None


async def test_stale_and_undated_jobs_follow_the_freshness_policy() -> None:
    company_id, source_id = await setup_source()
    result = await ingest(
        [
            job("10", hours_ago=100),
            job("11", hours_ago=800),
            job("12", "Data Infrastructure Engineer", hours_ago=None),
        ],
        company_id,
        source_id,
        complete=False,
    )
    assert (result.created, result.stale_skipped) == (2, 1)
    async with SessionLocal() as session:
        undated = await session.scalar(select(Job).where(Job.posted_at.is_(None)))
        assert undated.score_breakdown["freshness"] == 0
        assert any("does not publish a posting date" in reason for reason in undated.score_reasons)
