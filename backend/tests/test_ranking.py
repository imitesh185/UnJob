from datetime import UTC, datetime

from app.config import Settings
from app.schemas import IngestedJob
from app.services.normalization import normalize_job
from app.services.ranking import OpportunityScorer


def test_score_is_transparent_and_weighted() -> None:
    source = IngestedJob(
        company="Example",
        title="Data Platform Engineer",
        location="Remote India",
        remote_status="remote",
        application_url="https://example.test/apply",
        source_url="https://example.test/job",
        source="greenhouse",
        ats="greenhouse",
        external_id="42",
        posted_at=datetime.now(UTC),
        description="Required experience with Python, SQL, Spark, Kafka and Databricks.",
        raw_payload={},
    )
    result = OpportunityScorer(Settings()).score(normalize_job(source))
    assert result.overall >= 75
    assert set(result.breakdown) == set(Settings().scoring_weights)
    assert len(result.reasons) >= 4
