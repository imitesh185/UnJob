"""Direct Greenhouse/Lever board imports.

Request bodies are unchanged from Phase 1, but imports now run in the background worker:
the API validates and enqueues the work and returns the queued run (HTTP 202).
"""

from fastapi import APIRouter, status

from app.api.dependencies import SessionDep, SettingsDep
from app.discovery.settings_store import load_runtime_settings
from app.schemas import GreenhouseRequest, LeverRequest, RunRead
from app.services.queue import enqueue_run

router = APIRouter(prefix="/ingestion", tags=["ingestion"])


async def _enqueue(session, settings, provider: str, identifier: str) -> RunRead:
    runtime = await load_runtime_settings(session, settings)
    run, _created = await enqueue_run(
        session,
        "ingestion",
        params={"provider": provider, "identifier": identifier},
        budgets={**runtime.budgets.model_dump(), "max_pages": max(runtime.budgets.max_pages, 5)},
        dedupe_key=f"ingestion:{provider}:{identifier.lower()}",
        max_attempts=2,
    )
    return RunRead.model_validate(run)


@router.post("/greenhouse", response_model=RunRead, status_code=status.HTTP_202_ACCEPTED)
async def ingest_greenhouse(
    body: GreenhouseRequest, session: SessionDep, settings: SettingsDep
) -> RunRead:
    return await _enqueue(session, settings, "greenhouse", body.board_token)


@router.post("/lever", response_model=RunRead, status_code=status.HTTP_202_ACCEPTED)
async def ingest_lever(body: LeverRequest, session: SessionDep, settings: SettingsDep) -> RunRead:
    return await _enqueue(session, settings, "lever", body.company_slug)
