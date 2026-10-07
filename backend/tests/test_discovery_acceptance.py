"""Phase 2 acceptance: seeds are a starting point, not a boundary."""

from datetime import UTC, datetime, timedelta

import httpx
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import (
    CareerSource,
    Company,
    CompanyDiscoveryEvent,
    CompanyRelationship,
    DiscoveryCandidate,
    DiscoveryRun,
    Job,
)
from app.worker import run_worker
from tests.helpers import (
    FakeSearch,
    MockNetwork,
    empty_structured_sources,
    fast_settings,
    iso_hours_ago,
    make_explorer,
    respond,
)

TODAY = datetime.now(UTC)
WORKDAY = "/wday/cxs/mastercard/CorporateCareers"


def build_network() -> MockNetwork:
    network = MockNetwork()

    def greenhouse(request: httpx.Request) -> httpx.Response | None:
        if request.url.path == "/v1/boards/companyx":
            return respond({"name": "Company X"})
        if request.url.path == "/v1/boards/companyx/jobs":
            base = {"location": {"name": "Bengaluru, India"}, "company_name": "Company X"}
            return respond(
                {
                    "jobs": [
                        {
                            **base,
                            "id": 4001,
                            "title": "Senior Data Engineer",
                            "absolute_url": "https://boards.greenhouse.io/companyx/jobs/4001",
                            "first_published": iso_hours_ago(6),
                            "updated_at": iso_hours_ago(1),
                            "content": "&lt;p&gt;Build data pipelines with Spark, Kafka and Airflow on our lakehouse.&lt;/p&gt;",
                        },
                        {
                            **base,
                            "id": 4002,
                            "title": "Data Analyst",
                            "absolute_url": "https://boards.greenhouse.io/companyx/jobs/4002",
                            "first_published": iso_hours_ago(5),
                            "updated_at": iso_hours_ago(1),
                            "content": "Build dashboards in Tableau and Power BI for stakeholders and KPI reporting.",
                        },
                    ]
                }
            )
        return None

    network.add("boards-api.greenhouse.io", "/v1/boards/", greenhouse)

    def workday(request: httpx.Request) -> httpx.Response | None:
        if request.url.path == f"{WORKDAY}/jobs" and request.method == "POST":
            return respond(
                {
                    "total": 2,
                    "jobPostings": [
                        {
                            "title": "Senior Data Engineer",
                            "externalPath": "/job/Pune/Senior-Data-Engineer_R-1",
                            "postedOn": "Posted Today",
                            "locationsText": "Pune, India",
                        },
                        {
                            "title": "Data Center Technician",
                            "externalPath": "/job/Pune/Data-Center-Technician_R-2",
                            "postedOn": "Posted Today",
                            "locationsText": "Pune, India",
                        },
                    ],
                }
            )
        if request.url.path == f"{WORKDAY}/job/Pune/Senior-Data-Engineer_R-1":
            return respond(
                {
                    "jobPostingInfo": {
                        "title": "Senior Data Engineer",
                        "jobDescription": "<p>Design streaming data pipelines with Kafka and Spark.</p>",
                        "location": "Pune, India",
                        "startDate": TODAY.date().isoformat(),
                        "jobReqId": "R-1",
                        "externalUrl": "https://mastercard.wd1.myworkdayjobs.com/CorporateCareers/job/Pune/Senior-Data-Engineer_R-1",
                        "timeType": "Full time",
                    }
                }
            )
        return None

    network.add("mastercard.wd1.myworkdayjobs.com", "/wday/cxs/", workday)
    network.add(
        "careers.mastercard.com",
        "/",
        '<html><head><script src="a.js"></script></head><body><div id="root"></div>'
        + "<script></script>" * 6
        + "</body></html>",
    )
    network.add(
        "www.amazon.jobs",
        "/en/search.json",
        {
            "hits": 1,
            "jobs": [
                {
                    "id_icims": "999",
                    "title": "Data Engineer II, Ads Data Platform",
                    "posted_date": TODAY.strftime("%B %d, %Y"),
                    "description": "Build ETL pipelines with Spark and Redshift for petabyte-scale data.",
                    "normalized_location": "Bengaluru, Karnataka, IND",
                    "job_path": "/en/jobs/999/data-engineer-ii",
                }
            ],
        },
    )
    network.add(
        "hn.algolia.com",
        "/api/v1/search_by_date",
        {
            "hits": [
                {
                    "objectID": "9001",
                    "title": "Ask HN: Who is hiring? (October 2026)",
                    "created_at": iso_hours_ago(120),
                }
            ]
        },
    )
    network.add(
        "hn.algolia.com",
        "/api/v1/search",
        {
            "hits": [
                {
                    "objectID": "9002",
                    "parent_id": 9001,
                    "created_at": iso_hours_ago(30),
                    "comment_text": (
                        "Streamly | Senior Data Engineer | Bengaluru, India / REMOTE | Full-time<p>"
                        "We build streaming data infrastructure with Kafka and Flink. "
                        'Apply: <a href="https://jobs.lever.co/streamly">jobs.lever.co/streamly</a>'
                    ),
                }
            ]
        },
    )
    network.add(
        "api.lever.co",
        "/v0/postings/streamly",
        [
            {
                "id": "lv-1",
                "text": "Data Platform Engineer",
                "hostedUrl": "https://jobs.lever.co/streamly/lv-1",
                "applyUrl": "https://jobs.lever.co/streamly/lv-1/apply",
                "categories": {"location": "Bengaluru, India", "commitment": "Full-time"},
                "createdAt": int((TODAY - timedelta(hours=10)).timestamp() * 1000),
                "descriptionPlain": "Own our Kafka and Flink streaming data platform and data pipelines.",
                "lists": [],
                "workplaceType": "hybrid",
            }
        ],
    )
    network.add("remotive.com", "/api/remote-jobs", {"jobs": []})
    network.add("yc-oss.github.io", "/api/companies/hiring.json", [])
    return network


def build_search() -> FakeSearch:
    return FakeSearch(
        {
            '"Amazon" careers jobs': [
                (
                    "Amazon Jobs: Search open positions",
                    "https://www.amazon.jobs/en/",
                    "Find jobs at Amazon.",
                ),
            ],
            '"Mastercard" careers jobs': [
                ("Careers at Mastercard | Search Jobs", "https://careers.mastercard.com/us/en", ""),
                (
                    "Search for Jobs - Logo",
                    "https://mastercard.wd1.myworkdayjobs.com/CorporateCareers",
                    "",
                ),
            ],
            '"Company X" competitors': [
                (
                    "Company X competitors",
                    "https://www.example-directory.com/companyx/competitors",
                    "Top competitors of Company X include Company Y, Company Z and DataCo.",
                ),
            ],
            '"Data Engineer" India careers': [
                (
                    "Job Application for Senior Data Engineer at Company X",
                    "https://boards.greenhouse.io/companyx/jobs/4001",
                    "Bengaluru, India",
                ),
                (
                    "26,000 Data Engineer Job Vacancies in India | Indeed",
                    "https://in.indeed.com/q-data-engineer-l-india-jobs.html",
                    "",
                ),
            ],
            "site:linkedin.com": [
                (
                    "Acme Analytics hiring Senior Data Engineer in Bengaluru, Karnataka, India | LinkedIn",
                    "https://in.linkedin.com/jobs/view/senior-data-engineer-at-acme-analytics-3999",
                    "",
                ),
            ],
            "site:x.com": [
                (
                    'Jane on X: "We are hiring a Data Engineer at Streamly! Remote India"',
                    "https://x.com/jane/status/1",
                    "",
                ),
            ],
        }
    )


async def test_seeded_market_exploration_discovers_unseeded_companies_with_evidence() -> None:
    settings = fast_settings()
    network = build_network()
    search = build_search()
    explorer = make_explorer(settings, network, search, SessionLocal)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # This test exercises search-based resolution, so the career-portal registry is cleared.
        patch = await client.patch(
            "/api/v1/discovery/settings",
            json={"budgets": {"max_search_queries": 15}, "company_registry": []},
        )
        assert patch.status_code == 200, patch.text
        response = await client.post(
            "/api/v1/companies/seeds",
            json={"names": ["Amazon", "Flipkart", "Mastercard"], "tier": "A"},
        )
        assert response.status_code == 202, response.text
        seeded = response.json()
        assert {company["name"] for company in seeded["companies"]} == {
            "Amazon",
            "Flipkart",
            "Mastercard",
        }
        assert seeded["run_id"]
        assert network.requests == [], "The API must not perform remote I/O."

        processed = await run_worker(
            once=True, explorer=explorer, session_factory=SessionLocal, settings=settings
        )
        assert processed == 2  # seed resolution, then the follow-up market exploration

        async with SessionLocal() as session:
            runs = (
                await session.scalars(select(DiscoveryRun).order_by(DiscoveryRun.created_at))
            ).all()
            assert [run.kind for run in runs] == ["seed_resolution", "exploration"]
            assert [run.status for run in runs] == ["COMPLETED", "COMPLETED"], [
                run.errors for run in runs
            ]
            companies = {
                company.name: company for company in (await session.scalars(select(Company))).all()
            }

            # Unseeded companies were discovered from hiring evidence.
            discovered = {name for name, company in companies.items() if not company.is_seed}
            assert {
                "Company X",
                "Streamly",
                "Acme Analytics",
                "Company Y",
                "Company Z",
                "DataCo",
            } <= discovered
            assert "Indeed" not in companies and "LinkedIn" not in companies

            company_x = companies["Company X"]
            assert company_x.status == "ACTIVE"
            assert company_x.review_status == "PENDING"
            assert company_x.discovery_source == "fakesearch"
            events = (
                await session.scalars(
                    select(CompanyDiscoveryEvent).where(
                        CompanyDiscoveryEvent.company_id == company_x.id
                    )
                )
            ).all()
            reasons = {event.reason: event for event in events}
            assert (
                reasons["JOB_SEARCH"].source_url
                == "https://boards.greenhouse.io/companyx/jobs/4001"
            )
            assert (
                "Job Application for Senior Data Engineer at Company X"
                in reasons["JOB_SEARCH"].evidence
            )
            assert "CAREER_PAGE" in reasons
            source = await session.scalar(
                select(CareerSource).where(CareerSource.company_id == company_x.id)
            )
            assert (source.platform, source.scan_status) == ("greenhouse", "OK")
            assert source.last_complete_scan_at is not None
            jobs = (await session.scalars(select(Job).where(Job.company_id == company_x.id))).all()
            assert [job.title for job in jobs] == [
                "Senior Data Engineer"
            ]  # the analyst role is not targeted
            assert jobs[0].role_category == "data_engineering"
            assert jobs[0].score_breakdown["company"] > 0
            assert any("discovery score" in reason for reason in jobs[0].score_reasons)
            assert set(company_x.score_breakdown) == {
                "data_hiring_signal",
                "engineering_quality",
                "recent_hiring",
                "role_relevance",
                "india_remote_opportunity",
                "growth_potential",
            }
            assert "data-engineering opening" in company_x.discovery_reason

            # Similar-company discovery records the edge and its evidence; candidates wait in the frontier.
            company_y = companies["Company Y"]
            edge = await session.scalar(
                select(CompanyRelationship).where(
                    CompanyRelationship.target_company_id == company_y.id
                )
            )
            assert (
                edge.source_company_id == company_x.id and edge.relationship_type == "competes_with"
            )
            assert "Company Y" in edge.evidence
            candidate = await session.scalar(
                select(DiscoveryCandidate).where(DiscoveryCandidate.company_id == company_y.id)
            )
            assert (candidate.status, candidate.depth) == ("PENDING", 2)
            assert company_y.status == "DISCOVERED" and company_y.domain is None

            # Hiring posts via HN and X corroborate the same company.
            streamly = companies["Streamly"]
            streamly_events = (
                await session.scalars(
                    select(CompanyDiscoveryEvent.reason, CompanyDiscoveryEvent.source).where(
                        CompanyDiscoveryEvent.company_id == streamly.id
                    )
                )
            ).all()
            assert ("JOB_SEARCH", "hackernews") in [tuple(row) for row in streamly_events] or (
                "JOB_SEARCH" in streamly_events
            )
            assert streamly.status == "ACTIVE"

            # Seeds were resolved through the portal each company actually uses.
            mastercard_job = await session.scalar(
                select(Job).where(Job.company_id == companies["Mastercard"].id)
            )
            assert mastercard_job.ats == "workday" and mastercard_job.posted_at_precision == "date"
            assert companies["Mastercard"].domain == "mastercard.com"
            amazon_job = await session.scalar(
                select(Job).where(Job.company_id == companies["Amazon"].id)
            )
            assert amazon_job.ats == "amazon_jobs"
            assert not (
                await session.scalars(select(Job).where(Job.title.like("%Data Center%")))
            ).all()

            # Nothing is invented for companies that could not be verified.
            assert companies["Flipkart"].status == "UNVERIFIED"
            assert companies["Flipkart"].domain is None
            acme = companies["Acme Analytics"]
            assert acme.status == "UNVERIFIED" and acme.domain is None
            linkedin_event = await session.scalar(
                select(CompanyDiscoveryEvent).where(CompanyDiscoveryEvent.company_id == acme.id)
            )
            assert linkedin_event.reason == "LINKEDIN_HIRING_SIGNAL"
            assert linkedin_event.observation_count >= 2  # repeated observations, one event

        feed = (await client.get("/api/v1/discovery/feed")).json()
        assert any(
            item["company_name"] == "Company X" and item["status"] == "PENDING"
            for item in feed["items"]
        )
        summary = (await client.get("/api/v1/discovery/summary")).json()
        assert summary["companies_discovered_today"] >= 6
        assert (
            summary["high_confidence"] + summary["medium_confidence"] + summary["low_confidence"]
            == (summary["companies_discovered_today"])
        )
        assert summary["fresh_data_jobs"] >= 2
        hiring = (await client.get("/api/v1/companies", params={"data_hiring": "true"})).json()
        assert {"Company X", "Mastercard", "Amazon"} <= {item["name"] for item in hiring["items"]}
        detail = (await client.get(f"/api/v1/companies/{company_x.id}")).json()
        assert detail["sources"][0]["platform"] == "greenhouse"
        assert detail["fresh_data_roles_count"] == 1
        assert any(rel["target_company_name"] == "Company Y" for rel in detail["relationships"])
        jobs = (await client.get("/api/v1/jobs", params={"freshness": "fresh"})).json()
        assert all(item["freshness_status"] == "fresh" for item in jobs["items"])
        assert {item["company"] for item in jobs["items"]} >= {"Company X", "Streamly"}


async def test_blocked_homepage_falls_back_to_verified_jobs_site() -> None:
    settings = fast_settings()
    network = MockNetwork()
    network.add("www.amazon.com", "/", lambda _request: httpx.Response(503, text="blocked"))
    network.add("www.amazon.in", "/", lambda _request: httpx.Response(503, text="blocked"))
    network.add(
        "www.amazon.jobs",
        "/en/search.json",
        {
            "hits": 1,
            "jobs": [
                {
                    "id_icims": "777",
                    "title": "Data Engineer, Payments Data Platform",
                    "posted_date": TODAY.strftime("%B %d, %Y"),
                    "description": "Build Spark and Kafka data pipelines for the payments lakehouse.",
                    "normalized_location": "Hyderabad, Telangana, IND",
                    "job_path": "/en/jobs/777/data-engineer",
                }
            ],
        },
    )
    network.add(
        "www.amazon.jobs",
        "/",
        "<html><head><title>Amazon.jobs: Help us build Earth's most customer-centric company."
        "</title></head><body>Careers</body></html>",
    )
    empty_structured_sources(network)
    explorer = make_explorer(settings, network, FakeSearch(), SessionLocal)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        cleared = await client.patch("/api/v1/discovery/settings", json={"company_registry": []})
        assert cleared.status_code == 200, cleared.text
        response = await client.post("/api/v1/companies/seeds", json={"names": ["Amazon"]})
        assert response.status_code == 202, response.text

    await run_worker(once=True, explorer=explorer, session_factory=SessionLocal, settings=settings)

    async with SessionLocal() as session:
        amazon = await session.scalar(select(Company).where(Company.name == "Amazon"))
        # amazon.jobs proves the careers site, not the official company domain.
        assert amazon.domain is None
        source = await session.scalar(
            select(CareerSource).where(CareerSource.company_id == amazon.id)
        )
        assert (source.platform, source.discovered_via) == ("amazon_jobs", "homepage_probe")
        assert "Amazon.jobs" in source.evidence
        job = await session.scalar(select(Job).where(Job.company_id == amazon.id))
        assert job.ats == "amazon_jobs" and job.role_category == "data_engineering"
        assert amazon.status == "ACTIVE"
