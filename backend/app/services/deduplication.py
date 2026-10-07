from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job, JobSource
from app.services.normalization import NormalizedJob, normalize_text


class DeduplicationEngine:
    async def find_match(
        self, session: AsyncSession, candidate: NormalizedJob
    ) -> tuple[Job | None, bool]:
        source = candidate.source
        existing_source = await session.scalar(
            select(JobSource).where(
                JobSource.source == source.source,
                JobSource.external_id == source.external_id,
            )
        )
        if existing_source:
            return await session.get(Job, existing_source.job_id), True

        application_match = await session.scalar(
            select(Job).where(Job.application_url == source.application_url)
        )
        if application_match:
            return application_match, False

        candidates = (
            await session.scalars(
                select(Job).where(
                    Job.company == source.company,
                    Job.normalized_title == candidate.normalized_title,
                    Job.location == source.location,
                )
            )
        ).all()
        normalized_description = normalize_text(source.description)
        for job in candidates:
            similarity = SequenceMatcher(
                None, normalize_text(job.description)[:5000], normalized_description[:5000]
            ).ratio()
            if similarity >= 0.88:
                return job, False
        return None, False
