"""Phase 3 pipeline: profile import -> analysis -> priority -> auto-tailoring -> review -> tracking."""

import io
import uuid
import zipfile
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.discovery.settings_store import load_runtime_settings
from app.main import app
from app.models import Company, Job
from app.schemas import IngestedJob
from app.services.companies import new_company
from app.services.exploration import Explorer
from app.services.ingestion import DiscoveryService
from app.services.intelligence import IntelligenceService, sync_s_tier
from app.worker import run_worker
from tests.helpers import FakeSearch, MockNetwork, fast_settings, make_explorer, respond
from tests.intel_fixtures import RESUME_TEXT, SENIOR_JD, STREAMING_JD


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


def ingested(
    external_id: str, title: str, location: str, hours_ago: float, company: str
) -> IngestedJob:
    return IngestedJob(
        company=company,
        title=title,
        location=location,
        application_url=f"https://boards.greenhouse.io/acme/jobs/{external_id}",
        source_url=f"https://boards.greenhouse.io/acme/jobs/{external_id}",
        source="greenhouse",
        ats="greenhouse",
        external_id=external_id,
        posted_at=datetime.now(UTC) - timedelta(hours=hours_ago),
        description=STREAMING_JD + f"\nReference {external_id}.",
        raw_payload={"id": external_id},
    )


async def seed_jobs() -> dict[str, str]:
    settings = fast_settings()
    async with SessionLocal() as session:
        amazon = new_company(
            "Amazon", tier="UNCATEGORIZED", status="ACTIVE", review_status="ACCEPTED", is_seed=True
        )
        small = new_company(
            "Smallco Analytics", tier="UNCATEGORIZED", status="ACTIVE", review_status="ACCEPTED"
        )
        session.add_all([amazon, small])
        await session.flush()
        service = DiscoveryService(settings)
        await service.ingest_jobs(
            session,
            [
                ingested(
                    "a1",
                    "Senior Data Engineer, Streaming",
                    "Mumbai, Maharashtra, India",
                    5,
                    "Amazon",
                ),
                ingested(
                    "a2",
                    "Senior Data Engineer, Payments Streaming",
                    "Seattle, WA, USA",
                    5,
                    "Amazon",
                ),
            ],
            company=amazon,
        )
        await service.ingest_jobs(
            session,
            [
                ingested("s1", "Data Engineer", "Bengaluru, India", 30, "Smallco Analytics"),
            ],
            company=small,
        )
        runtime = await load_runtime_settings(session, settings)
        await sync_s_tier(session, runtime)
        jobs = {
            job.title + "|" + job.location: str(job.id)
            for job in (await session.scalars(select(Job))).all()
        }
        return jobs


async def test_s_tier_jobs_are_tailored_automatically_and_need_human_approval(client) -> None:
    imported = await client.post(
        "/api/v1/profile/resume/text", json={"text": RESUME_TEXT, "filename": "jane.txt"}
    )
    assert imported.status_code == 201, imported.text
    profile = imported.json()
    assert profile["candidate"]["full_name"] == "Jane Doe"
    assert profile["import"]["analysis_run_id"]
    assert {skill["name"]: skill["level"] for skill in profile["skills"]}["Hadoop"] == "RUSTY"
    patched = await client.patch(
        "/api/v1/profile",
        json={"preferred_locations": ["Mumbai", "Pune", "India", "Remote (India)"]},
    )
    assert patched.status_code == 200, patched.text
    jobs = await seed_jobs()
    async with SessionLocal() as session:
        amazon = await session.scalar(select(Company).where(Company.name == "Amazon"))
        assert (amazon.tier, amazon.tier_source) == ("S", "s_tier_list")

    settings = fast_settings(data_dir=get_settings().data_dir)
    explorer = Explorer(settings, SessionLocal)
    processed = await run_worker(
        once=True, explorer=explorer, session_factory=SessionLocal, settings=settings
    )
    assert processed == 1  # one full candidate analysis (profile import and update are merged)

    mumbai = jobs["Senior Data Engineer, Streaming|Mumbai, Maharashtra, India"]
    seattle = jobs["Senior Data Engineer, Payments Streaming|Seattle, WA, USA"]
    listing = (await client.get("/api/v1/opportunities")).json()
    by_id = {item["job_id"]: item for item in listing["items"]}
    assert mumbai in by_id and seattle not in by_id  # location mismatch is rejected
    top = by_id[mumbai]
    assert top["priority_class"] in {"P0", "P1"} and top["tier"] == "S"
    assert (
        top["resume"] and top["resume"]["truth_passed"] and top["resume"]["status"] == "USER_REVIEW"
    )
    rejected = (
        await client.get("/api/v1/opportunities", params={"include_rejected": "true"})
    ).json()
    far = next(item for item in rejected["items"] if item["job_id"] == seattle)
    assert far["priority_class"] == "REJECT" and far["resume"] is None
    assert any("Location" in gate for gate in far["gates"])

    today = (await client.get("/api/v1/opportunities/today")).json()
    assert today["plan"][0]["job_id"] == mumbai and today["plan_minutes"] <= 135
    assert today["s_tier"]["tailored"] >= 1 and today["awaiting_review"] >= 1

    detail = (await client.get(f"/api/v1/opportunities/{mumbai}")).json()
    assert detail["analysis"]["years_min"] == 4
    assert detail["match"]["skill_assessments"] and detail["priority"]["strategy"]["emphasize"]
    assert detail["company_target"]["score"] > 0
    variant_id = detail["resume"]["id"]
    variant = (await client.get(f"/api/v1/resume-variants/{variant_id}")).json()
    assert variant["master_blocks"] and variant["blocks"][0]["value"] == "Jane Doe"
    assert variant["changes"]["summary_changed"] is True
    assert all(claim["source_facts"] for claim in variant["claims"] if claim["section"] != "header")
    assert {check["check"] for check in variant["quality_checks"]} >= {"truth", "ats", "seniority"}
    download = await client.get(
        f"/api/v1/resume-variants/{variant_id}/download", params={"format": "docx"}
    )
    assert download.status_code == 200
    assert "word/document.xml" in zipfile.ZipFile(io.BytesIO(download.content)).namelist()

    application = detail["application"]
    assert application["status"] == "USER_REVIEW"
    blocked = await client.patch(
        f"/api/v1/applications/{application['id']}", json={"status": "APPLIED"}
    )
    assert blocked.status_code == 409  # nothing can be marked applied before approval

    bullet = next(
        claim
        for claim in variant["claims"]
        if claim["section"] == "experience" and claim["included"]
    )
    edited = await client.patch(
        f"/api/v1/resume-variants/{variant_id}/claims/{bullet['id']}",
        json={"text": bullet["text"] + " Deployed on Kubernetes."},
    )
    assert edited.status_code == 200
    assert edited.json()["truth_passed"] is False
    assert (await client.post(f"/api/v1/resume-variants/{variant_id}/approve")).status_code == 409
    excluded = await client.patch(
        f"/api/v1/resume-variants/{variant_id}/claims/{bullet['id']}", json={"included": False}
    )
    assert excluded.json()["truth_passed"] is True
    approved = await client.post(f"/api/v1/resume-variants/{variant_id}/approve")
    assert approved.status_code == 200 and approved.json()["status"] == "APPROVED"

    # The JD changes after approval: the regenerated resume must be approved again.
    async with SessionLocal() as session:
        job = await session.get(Job, uuid.UUID(mumbai))
        job.description = SENIOR_JD
        await session.commit()
    service = IntelligenceService(settings, SessionLocal)
    replacement, created = await service.tailor(uuid.UUID(mumbai), trigger="manual")
    assert created and replacement.version == 2
    reopened = (await client.get(f"/api/v1/applications/{application['id']}")).json()
    assert reopened["status"] == "USER_REVIEW"
    assert (await client.get(f"/api/v1/resume-variants/{variant_id}")).json()["status"] == "STALE"
    still_blocked = await client.patch(
        f"/api/v1/applications/{application['id']}", json={"status": "APPLIED"}
    )
    assert still_blocked.status_code == 409
    reapproved = await client.post(f"/api/v1/resume-variants/{replacement.id}/approve")
    assert reapproved.status_code == 200, reapproved.text

    applied = await client.patch(
        f"/api/v1/applications/{application['id']}", json={"status": "APPLIED"}
    )
    assert applied.status_code == 200 and applied.json()["applied_at"]
    screen = await client.patch(
        f"/api/v1/applications/{application['id']}",
        json={"status": "RECRUITER_SCREEN", "note": "Recruiter called"},
    )
    assert [event["to_status"] for event in screen.json()["events"]][-3:] == [
        "APPROVED",
        "APPLIED",
        "RECRUITER_SCREEN",
    ]
    analytics = (await client.get("/api/v1/applications/analytics")).json()
    assert analytics["totals"]["applied"] == 1 and analytics["totals"]["interviews"] == 1
    assert analytics["by_tier"][0]["value"] == "S" and analytics["by_tier"][0]["low_sample"] is True

    companies = (await client.get("/api/v1/companies", params={"sort": "target"})).json()
    amazon_row = next(item for item in companies["items"] if item["name"] == "Amazon")
    assert amazon_row["target_score"] is not None and amazon_row["tier"] == "S"
    summary = (await client.get("/api/v1/dashboard/summary")).json()
    assert summary["applications_submitted"] == 1 and summary["interviews"] == 1


async def test_profile_requires_a_readable_resume(client) -> None:
    bad = await client.post(
        "/api/v1/profile/resume", files={"file": ("resume.png", b"\x89PNG....", "image/png")}
    )
    assert bad.status_code == 422
    assert (await client.get("/api/v1/opportunities")).json()["profile_ready"] is False
    assert (await client.post("/api/v1/opportunities/analyze")).status_code == 409


async def test_registry_resolves_an_s_tier_alias_to_its_career_portal(client) -> None:
    host = "jpmc.fa.oraclecloud.com"
    network = MockNetwork()

    def oracle(request: httpx.Request) -> httpx.Response | None:
        if request.url.path.endswith("/recruitingCEJobRequisitions"):
            return respond({"items": [{"TotalJobsCount": 0, "requisitionList": []}]})
        return None

    network.add(host, "/hcmRestApi/", oracle)
    for name in ("hn.algolia.com", "remotive.com", "yc-oss.github.io"):
        network.add(name, "/", {"hits": [], "jobs": []} if name != "yc-oss.github.io" else [])
    explorer = make_explorer(fast_settings(), network, FakeSearch(), SessionLocal)
    response = await client.post("/api/v1/companies/seeds", json={"names": ["JPMC"]})
    assert response.status_code == 202
    company = response.json()["companies"][0]
    assert company["tier"] == "S" and company["tier_source"] == "s_tier_list"
    await run_worker(
        once=True,
        explorer=explorer,
        session_factory=SessionLocal,
        settings=fast_settings(),
        max_runs=1,
    )
    detail = (await client.get(f"/api/v1/companies/{company['id']}")).json()
    source = detail["sources"][0]
    assert source["platform"] == "oracle" and source["discovered_via"] == "registry"
    assert source["scan_status"] == "OK"
