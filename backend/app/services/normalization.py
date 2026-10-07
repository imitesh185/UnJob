import re
from dataclasses import dataclass

from app.schemas import IngestedJob

TECH_ALIASES = {
    "Azure Data Lake Storage": ("azure data lake", "adls", "adls gen2"),
    "Apache Spark": ("apache spark", "pyspark", "spark"),
    "Apache Kafka": ("apache kafka", "kafka"),
    "Azure Data Factory": ("azure data factory", "adf"),
    "Databricks": ("azure databricks", "databricks"),
    "Airflow": ("apache airflow", "airflow"),
    "Python": ("python",),
    "SQL": ("sql",),
    "PostgreSQL": ("postgresql", "postgres"),
    "Kubernetes": ("kubernetes", "k8s"),
    "Terraform": ("terraform",),
}

ROLE_REPLACEMENTS = {
    "sr": "senior",
    "sde": "software engineer",
    "big data": "data",
}


@dataclass(frozen=True)
class NormalizedJob:
    source: IngestedJob
    normalized_title: str
    requirements: list[str]
    preferred_requirements: list[str]
    technology_stack: list[str]
    experience_requirement: str | None


def normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def normalize_title(title: str) -> str:
    normalized = normalize_text(title)
    for old, new in ROLE_REPLACEMENTS.items():
        normalized = re.sub(rf"\b{re.escape(old)}\b", new, normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def normalize_job(job: IngestedJob) -> NormalizedJob:
    text = normalize_text(f"{job.title} {job.description}")
    technologies = [
        canonical
        for canonical, aliases in TECH_ALIASES.items()
        if any(re.search(rf"\b{re.escape(alias)}\b", text) for alias in aliases)
    ]
    sentences = re.split(r"(?<=[.!?])\s+|\s*[•·]\s*", job.description)
    requirements = [
        sentence.strip()
        for sentence in sentences
        if re.search(r"\b(required|must|minimum|experience with|proficien)\b", sentence, re.I)
    ][:20]
    preferred = [
        sentence.strip()
        for sentence in sentences
        if re.search(r"\b(preferred|nice to have|bonus)\b", sentence, re.I)
    ][:20]
    experience = re.search(
        r"\b(\d+\s*(?:\+|-\s*\d+)?\s*years?(?:\s+of)?\s+experience)\b",
        job.description,
        re.I,
    )
    return NormalizedJob(
        source=job,
        normalized_title=normalize_title(job.title),
        requirements=requirements,
        preferred_requirements=preferred,
        technology_stack=technologies,
        experience_requirement=experience.group(1) if experience else None,
    )
