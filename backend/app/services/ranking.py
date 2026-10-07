from datetime import UTC, datetime

from app.config import Settings
from app.discovery.jsonld import age_bounds_hours
from app.discovery.roles import TARGET_CATEGORIES, RoleClassification, classify_role
from app.schemas import ScoreResult
from app.services.normalization import NormalizedJob

TARGET_ROLES = (
    "data engineer",
    "data platform engineer",
    "data infrastructure engineer",
    "big data engineer",
    "distributed systems engineer",
    "platform engineer",
    "software engineer data",
    "software engineer infrastructure",
)
CORE_TECH = {
    "Azure Data Lake Storage",
    "Apache Spark",
    "Apache Kafka",
    "Azure Data Factory",
    "Databricks",
    "Python",
    "SQL",
}
LOW_FRICTION_ATS = {"greenhouse", "lever", "ashby", "recruitee", "smartrecruiters"}
ACCOUNT_REQUIRED_ATS = {"workday", "oracle", "oracle_taleo", "icims", "amazon_jobs"}


class OpportunityScorer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def score(
        self,
        job: NormalizedJob,
        classification: RoleClassification | None = None,
        company_score: float | None = None,
        company_label: str | None = None,
    ) -> ScoreResult:
        classification = classification or classify_role(
            job.source.title, job.source.description, job.source.company
        )
        related_role = classification.is_target
        relevance = (
            classification.relevance
            if classification.category in TARGET_CATEGORIES
            else min(classification.relevance, 0.35)
        )
        role_alignment = round(100.0 * relevance, 1)

        matched = CORE_TECH.intersection(job.technology_stack)
        jd_fit = min(100.0, 45.0 + len(matched) * 8.0 + (15.0 if related_role else 0))
        technical_value = min(
            100.0,
            45.0
            + len(job.technology_stack) * 5.0
            + (15.0 if {"Apache Spark", "Apache Kafka"} & matched else 0),
        )
        freshness = self._freshness(job.source.posted_at, job.source.posted_at_precision)
        company = 50.0 if company_score is None else max(0.0, min(100.0, company_score))
        compensation = 65.0 if job.source.salary else 45.0
        if job.source.ats in LOW_FRICTION_ATS:
            application_friction = 85.0
        elif job.source.ats in ACCOUNT_REQUIRED_ATS:
            application_friction = 55.0
        else:
            application_friction = 60.0
        breakdown = {
            "jd_fit": jd_fit,
            "technical_value": technical_value,
            "freshness": freshness,
            "company": company,
            "compensation": compensation,
            "role_alignment": role_alignment,
            "application_friction": application_friction,
        }
        overall = sum(
            breakdown[name] * weight for name, weight in self.settings.scoring_weights.items()
        )
        reasons = [
            f"Role alignment is {int(role_alignment)}/100 for '{job.source.title}': "
            f"{classification.reasons[0] if classification.reasons else classification.category}",
            (
                f"Matched target technologies: {', '.join(sorted(matched))}."
                if matched
                else "No target technologies were detected in the public description."
            ),
            (
                f"Freshness is {int(freshness)}/100 based on the source posting date."
                if job.source.posted_at
                else "Freshness is 0/100 because the source does not publish a posting date."
            ),
            (
                f"Company component uses the evidence-based discovery score {company:.0f}/100"
                + (f" for {company_label}." if company_label else ".")
                if company_score is not None
                else "Company quality is neutral until verified company research is available."
            ),
            (
                "Compensation is present in the source."
                if job.source.salary
                else "Compensation is unknown and receives a conservative score."
            ),
        ]
        return ScoreResult(
            overall=round(overall, 1),
            breakdown={key: round(value, 1) for key, value in breakdown.items()},
            reasons=reasons,
        )

    def _freshness(self, posted_at: datetime | None, precision: str | None = None) -> float:
        bounds = age_bounds_hours(posted_at, precision, datetime.now(UTC))
        if bounds is None:
            return 0.0
        # Uses the oldest possible age so date-only postings are never over-rewarded.
        return max(0.0, 100.0 * (1 - bounds[1] / self.settings.freshness_hours))
