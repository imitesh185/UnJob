from datetime import UTC, datetime

from app.schemas import IngestedJob
from app.services.normalization import normalize_job, normalize_title


def make_job(description: str) -> IngestedJob:
    return IngestedJob(
        company="Example",
        title="Sr. Big Data Engineer",
        application_url="https://example.test/apply",
        source_url="https://example.test/job",
        source="test",
        ats="test",
        external_id="1",
        posted_at=datetime.now(UTC),
        description=description,
        raw_payload={},
    )


def test_normalizes_role_and_semantic_technology_aliases() -> None:
    job = normalize_job(
        make_job("Required: 5+ years experience with ADLS Gen2, PySpark, ADF and Kafka.")
    )
    assert normalize_title("Sr. Big Data Engineer") == "senior data engineer"
    assert {
        "Azure Data Lake Storage",
        "Apache Spark",
        "Azure Data Factory",
        "Apache Kafka",
    }.issubset(job.technology_stack)
    assert job.experience_requirement == "5+ years experience"
