import pytest

from app.discovery.ats import detect_from_html, detect_from_url, detect_platform
from app.discovery.extraction import extract_signal, extract_similar
from app.discovery.jsonld import find_job_postings, posting_fields
from app.discovery.names import company_key, name_matches_domain, registrable_domain
from app.discovery.roles import classify_role
from app.discovery.search import SearchResult


@pytest.mark.parametrize(
    ("title", "description", "category", "target"),
    [
        ("Data Engineer", None, "data_engineering", True),
        ("Backend Engineer - Data Infrastructure", None, "data_engineering", True),
        ("Software Engineer, Data", None, "data_engineering", True),
        ("Distributed Systems Engineer", None, "platform_infrastructure", True),
        (
            "Software Engineer",
            "Build data pipelines with Spark, Kafka, Airflow and Snowflake in our data lake.",
            "data_engineering",
            True,
        ),
        ("Data Analyst", None, "adjacent_data", False),
        ("Senior Data Center Engineer", None, "non_target", False),
        ("SAP MM/PP Master Data Engineer", None, "adjacent_data", False),
        ("Sales Engineer - Data Platform", None, "non_target", False),
        (
            "Business Intelligence Engineer, eCS Data Engineering and Analytics",
            None,
            "adjacent_data",
            False,
        ),
        ("Data Analyst - Data Engineering Team", None, "adjacent_data", False),
        (
            "Product Analytics - Developer Experience, Bridge",
            "Databricks ETL data pipelines data lake",
            "adjacent_data",
            False,
        ),
        (
            "Abuse Research Engineer",
            "Spark Databricks Presto data pipelines across the platform",
            "other_engineering",
            False,
        ),
        ("DevOps Engineer", None, "platform_infrastructure", False),
        ("Data Engineer - Analytics Platform", None, "data_engineering", True),
        (
            "Senior Interconnect Product Data Engineer",
            "Manage interconnect cables, PCB bill of materials, PLM and manufacturing data for silicon "
            "products across facilities and the hardware product lifecycle.",
            "adjacent_data",
            False,
        ),
    ],
)
def test_role_classification_is_semantic_not_title_only(
    title, description, category, target
) -> None:
    result = classify_role(title, description)
    assert (result.category, result.is_target) == (category, target)
    assert result.reasons


def result(title: str, url: str, snippet: str = "") -> SearchResult:
    return SearchResult(
        title=title, url=url, snippet=snippet, provider="duckduckgo", query="q", rank=1
    )


@pytest.mark.parametrize(
    ("title", "url", "expected"),
    [
        (
            "Data Engineer - Accenture",
            "https://www.accenture.com/in-en/careers/jobdetails?id=ATCI-1",
            ("Accenture", "accenture.com"),
        ),
        (
            "Job Application for Data Engineer at Sidecar Health",
            "https://boards.greenhouse.io/sidecarhealth/jobs/5",
            ("Sidecar Health", None),
        ),
        (
            "Flipkart Data Engineer Jobs - Naukri.com",
            "https://www.naukri.com/flipkart-data-engineer-jobs",
            ("Flipkart", None),
        ),
        (
            "4 Flipkart.com Data Engineer jobs in India - LinkedIn India",
            "https://in.linkedin.com/jobs/flipkart.com-data-engineer-jobs",
            ("Flipkart", None),
        ),
    ],
)
def test_employers_are_extracted_from_observed_results(title, url, expected) -> None:
    signal = extract_signal(result(title, url))
    assert signal is not None
    assert (signal.company_name, signal.domain) == expected
    assert signal.source_url == url and title in signal.evidence


@pytest.mark.parametrize(
    ("title", "url"),
    [
        (
            "26,000 Data Engineer Job Vacancies in India | Indeed",
            "https://in.indeed.com/q-data-engineer-l-india-jobs.html",
        ),
        (
            "Linkedin Data Engineer Jobs in India (854 Open Roles) | LinkedIn",
            "https://in.linkedin.com/jobs/linkedin-data-engineer-jobs",
        ),
        (
            "Data Engineer Jobs in India \u2013 Hiring Now - Switchly",
            "https://www.switchly.in/data-engineer-jobs-india",
        ),
        ("What is data? - IBM", "https://www.ibm.com/think/topics/data"),
        (
            "What does a data engineer do?",
            "https://www.coursera.org/articles/what-does-a-data-engineer-do",
        ),
        ("Search for Jobs - Logo", "https://mastercard.wd1.myworkdayjobs.com/CorporateCareers"),
        ("Data Analyst - Acme", "https://acme.com/careers/jobs/1"),
        ("Early-stage Startup | Data Engineer | Remote", "https://news.example.org/hiring/1"),
        (
            "Early-stage Startup | Data Engineer | Remote",
            "https://example.org/careers/data-engineer",
        ),
    ],
)
def test_listing_pages_aggregators_and_non_hiring_pages_create_no_company(title, url) -> None:
    assert extract_signal(result(title, url)) is None


def test_site_owner_is_named_only_for_its_own_careers_pages() -> None:
    signal = extract_signal(
        result("Data Engineer | Careers", "https://www.perfios.com/careers/data-engineer")
    )
    assert (signal.company_name, signal.domain, signal.confidence) == (
        "Perfios",
        "perfios.com",
        0.45,
    )
    assert "inferred from the domain" in signal.evidence


def test_hiring_posts_and_similar_companies_keep_low_confidence_evidence() -> None:
    from app.discovery.extraction import clean_company_candidate

    assert clean_company_candidate("Snout https://snout.com/") == "Snout"
    assert clean_company_candidate("Early-stage Startup") is None
    post = extract_signal(
        result(
            'Jane on X: "We are hiring a Data Engineer at Streamly!"', "https://x.com/jane/status/1"
        ),
        "x",
    )
    assert (post.company_name, post.reason, post.confidence) == (
        "Streamly",
        "X_HIRING_SIGNAL",
        0.35,
    )
    similar = extract_similar(
        result(
            "Perfios competitors",
            "https://example.org/perfios",
            "Top competitors of Perfios include Razorpay, Cashfree and others.",
        ),
        "Perfios",
    )
    assert [(item.company_name, item.related_company) for item in similar] == [
        ("Razorpay", "Perfios"),
        ("Cashfree", "Perfios"),
    ]


def test_company_domains_require_name_evidence() -> None:
    assert registrable_domain("careers.example.co.in") == "example.co.in"
    assert name_matches_domain("Amazon", "amazon.jobs") == 1.0
    assert name_matches_domain("Flipkart", "flipkartcareers.com") == 0.8
    assert name_matches_domain("Perfios Software Solutions", "perfios.com") == 0.7
    assert name_matches_domain("Visa", "visaguide.world") == 0.0
    assert name_matches_domain("Acme", "linkedin.com") == 0.0
    assert company_key("JPMorgan Chase & Co.") == "jpmorganchase"
    assert company_key("Flipkart.com") == "flipkart"


@pytest.mark.parametrize(
    ("url", "platform", "identifier"),
    [
        ("https://boards.greenhouse.io/stripe/jobs/1", "greenhouse", {"token": "stripe"}),
        ("https://boards.greenhouse.io/embed/job_board?for=acme", "greenhouse", {"token": "acme"}),
        ("https://jobs.lever.co/figma/abc", "lever", {"slug": "figma", "region": "global"}),
        ("https://jobs.ashbyhq.com/linear/123", "ashby", {"slug": "linear"}),
        (
            "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/x",
            "workday",
            {
                "host": "nvidia.wd5.myworkdayjobs.com",
                "tenant": "nvidia",
                "site": "NVIDIAExternalCareerSite",
            },
        ),
        (
            "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/1",
            "oracle",
            {"host": "jpmc.fa.oraclecloud.com", "site": "CX_1001", "lang": "en"},
        ),
        ("https://jobs.smartrecruiters.com/Visa/7440000", "smartrecruiters", {"company": "Visa"}),
        ("https://careers-acme.icims.com/jobs/1/job", "icims", {"host": "careers-acme.icims.com"}),
        ("https://jobs.jobvite.com/acme/job/o1", "jobvite", {"company": "acme"}),
        ("https://acme.teamtailor.com/jobs/1", "teamtailor", {"host": "acme.teamtailor.com"}),
        ("https://acme.recruitee.com/o/data-engineer", "recruitee", {"slug": "acme"}),
        ("https://www.amazon.jobs/en/jobs/1", "amazon_jobs", {}),
        (
            "https://acme.taleo.net/careersection/2/jobdetail.ftl",
            "oracle_taleo",
            {"host": "acme.taleo.net"},
        ),
    ],
)
def test_platforms_are_detected_from_urls(url, platform, identifier) -> None:
    match = detect_from_url(url)
    assert (match.platform, match.identifier) == (platform, identifier)


def test_platforms_are_detected_from_custom_career_pages() -> None:
    embedded = '<script src="https://boards.greenhouse.io/embed/job_board/js?for=acme"></script>'
    assert detect_from_html("https://acme.com/careers", embedded).identifier == {"token": "acme"}
    workday = '<a href="https://acme.wd3.myworkdayjobs.com/en-US/External">Jobs</a>'
    assert detect_from_html("https://acme.com/careers", workday).platform == "workday"
    assert (
        detect_platform(
            "https://acme.com/careers",
            '<script type="application/ld+json">{"@type":"JobPosting"}</script>',
        ).platform
        == "schema_org"
    )
    assert (
        detect_platform("https://acme.com/careers", "<html>No jobs here</html>").platform
        == "generic_html"
    )
    assert detect_platform("https://acme.com/careers").platform == "unknown"


def test_schema_org_job_postings_are_parsed_without_inventing_fields() -> None:
    page = """<script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"JobPosting",
      "title":"Senior Data Engineer","datePosted":"2026-10-06","validThrough":"2026-11-06T00:00:00Z",
      "description":"&lt;p&gt;Spark and Kafka&lt;/p&gt;","hiringOrganization":{"@type":"Organization","name":"Acme"},
      "jobLocation":{"@type":"Place","address":{"addressLocality":"Pune","addressCountry":"IN"}},
      "employmentType":["FULL_TIME"],"identifier":{"value":"R-9"}}]}</script>"""
    postings = find_job_postings(page)
    assert len(postings) == 1
    fields = posting_fields(postings[0], "https://acme.com/jobs/9")
    assert (fields["title"], fields["company_name"], fields["identifier"]) == (
        "Senior Data Engineer",
        "Acme",
        "R-9",
    )
    assert fields["precision"] == "date" and fields["location"] == "Pune, IN"
    assert fields["description"] == "Spark and Kafka"
    assert fields["salary"] is None and fields["remote"] is False
