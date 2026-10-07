from datetime import UTC, datetime

from sqlalchemy import func, select

from app.config import Settings
from app.connectors.base import JobConnector
from app.db import Base, SessionLocal, engine
from app.models import Job, JobSource
from app.schemas import IngestedJob
from app.services.ingestion import DiscoveryService


class StaticConnector(JobConnector):
    def __init__(self, job: IngestedJob) -> None:
        self.job = job

    async def fetch(self, identifier: str) -> list[IngestedJob]:
        return [self.job]


def source_job(source: str, external_id: str) -> IngestedJob:
    return IngestedJob(
        company="Canonical Co",
        title="Data Platform Engineer",
        location="Remote India",
        remote_status="remote",
        application_url="https://canonical.example/jobs/42/apply",
        source_url=f"https://{source}.example/jobs/{external_id}",
        source=source,
        ats=source,
        external_id=external_id,
        posted_at=datetime.now(UTC),
        description="Required experience with Python, SQL, Spark, Kafka and Databricks.",
        raw_payload={"id": external_id},
    )


async def test_ingestion_is_idempotent_and_retains_all_sources() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    service = DiscoveryService(Settings())
    async with SessionLocal() as session:
        first = await service.ingest(
            session, StaticConnector(source_job("greenhouse", "gh-42")), "unused"
        )
    async with SessionLocal() as session:
        second = await service.ingest(
            session, StaticConnector(source_job("lever", "lever-42")), "unused"
        )
        job_count = await session.scalar(select(func.count(Job.id)))
        source_count = await session.scalar(select(func.count(JobSource.id)))

    assert first.created == 1
    assert second.created == 0
    assert second.deduplicated == 1
    assert job_count == 1
    assert source_count == 2

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
