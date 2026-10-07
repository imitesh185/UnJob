"""Phase 3 unit tests: resume facts, JD analysis, matching, priority, tailoring and truth checks."""

import io
import json
import zipfile
from datetime import date
from types import SimpleNamespace

import httpx

from app.config import Settings
from app.intelligence.facts import aggregate_skills, build_facts, candidate_domains
from app.intelligence.jd_analyzer import analyze_job
from app.intelligence.llm import LLMRewriter, RewriteRequest
from app.intelligence.matching import build_candidate_model, match_job
from app.intelligence.positioning import positioning_for
from app.intelligence.quality import evaluate_resume
from app.intelligence.render import to_docx, to_markdown, to_text
from app.intelligence.resume_parser import extract_text, parse_resume, years_of_experience
from app.intelligence.tailoring import build_resume_base, master_resume, tailor_resume
from app.intelligence.targeting import application_priority, freshness_score
from app.intelligence.taxonomy import find_terms
from app.intelligence.verification import verify_claim
from tests.intel_fixtures import RESUME_TEXT, SENIOR_JD, STREAMING_JD

TODAY = date(2026, 10, 7)


def resume_facts():
    text, _parser = extract_text(RESUME_TEXT.encode(), "resume.txt")
    parsed = parse_resume(text)
    facts = [
        SimpleNamespace(
            id=f"f{index}", active=True, **{k: getattr(d, k) for k in d.__dataclass_fields__}
        )
        for index, d in enumerate(build_facts(parsed))
    ]
    return parsed, facts


def candidate(parsed, facts, **overrides):
    values = {
        "full_name": parsed.name,
        "email": parsed.email,
        "phone": parsed.phone,
        "links": parsed.links,
        "years_experience": years_of_experience(parsed.experiences, TODAY),
        "target_roles": ["Data Engineer", "Senior Data Engineer"],
        "preferred_locations": ["Mumbai", "Pune", "India", "Remote (India)"],
        "remote_preference": None,
        "open_to_relocation": None,
        "compensation_min": None,
        "compensation_currency": None,
        "domains": candidate_domains(facts),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def model_for(**overrides):
    parsed, facts = resume_facts()
    person = candidate(parsed, facts, **overrides)
    return parsed, facts, person, build_candidate_model(person, facts, TODAY)


def test_resume_parser_keeps_verbatim_facts_and_infers_levels_from_evidence() -> None:
    parsed, facts = resume_facts()
    assert parsed.name == "Jane Doe" and parsed.email == "jane.doe@example.com"
    current, old = parsed.experiences
    assert (current.employer, current.title, current.location) == (
        "Acme Data Services",
        "Senior Analyst (Senior Data Engineer)",
        "Mumbai, India",
    )
    assert (current.start, current.end, old.start, old.end) == (
        "2023-03",
        "present",
        "2016-01",
        "2019-12",
    )
    assert current.stack == ["Python", "PySpark", "Kafka", "SQL", "Azure", "Databricks"]
    assert years_of_experience(parsed.experiences, TODAY) == 7.5
    bullets = [fact for fact in facts if fact.category == "experience"]
    # Statements are the resume's own words, never paraphrased.
    assert bullets[0].statement.startswith("Built Kafka-based streaming ingestion")
    assert bullets[0].label == "Streaming Ingestion" and "35%" in bullets[0].metrics
    assert all(fact.verified for fact in facts)
    skills = aggregate_skills(facts, TODAY, rusty_after_years=3)
    assert skills["Kafka"].level == "STRONG" and skills["Spark"].level == "STRONG"
    assert skills["Hadoop"].level == "RUSTY"  # production use ended in 2019
    assert skills["Airflow"].level == "PROJECT"  # projects only, never production
    assert skills["Java"].level == "LISTED"
    assert "Streaming / real-time processing" not in skills  # concepts are tracked separately
    education = [fact for fact in facts if fact.category == "education"]
    assert "University of Pune" in education[0].statement and "2015" in education[0].statement


def test_jd_analysis_separates_required_preferred_and_alternatives() -> None:
    analysis = analyze_job(
        "Senior Data Engineer",
        STREAMING_JD + "\nSalary: \u20b930 LPA - \u20b945 LPA",
        location="Bengaluru, Karnataka, India",
        company_name="Kafka Corp",
    )
    requirements = {item["text"]: item for item in analysis.requirements}
    aws = next(item for text, item in requirements.items() if "AWS technologies" in text)
    assert aws["kind"] == "required" and aws["mode"] == "any"  # "such as" lists alternatives
    assert {"Amazon Redshift", "Amazon S3"} <= set(aws["technologies"])
    assert analysis.years_min == 4 and analysis.seniority == "senior"
    preferred = {skill["name"] for skill in analysis.preferred_skills}
    assert {"Kubernetes", "Apache Iceberg", "Airflow"} <= preferred
    required = {skill["name"] for skill in analysis.required_skills}
    assert {"Spark", "Python", "SQL"} <= required
    assert analysis.location_requirements["india"] is True
    assert analysis.compensation["currency"] == "INR" and analysis.compensation["min"] == 3_000_000
    assert "Payments & financial services" in analysis.domains or analysis.business_context
    # The hiring company's own name is not a skill requirement.
    company = analyze_job(
        "Data Engineer",
        "Basic qualifications\n- Experience with Snowflake and SQL",
        company_name="Snowflake",
    )
    assert "Snowflake" not in {skill["name"] for skill in company.required_skills}
    # A years range in the title is the headline ask, even if the body says "3+ years".
    junior = analyze_job(
        "Data Engineer (1 - 2 years of experience in Java / Python, Kafka)",
        "Basic qualifications\n- Bachelor's degree, OR 3+ years of relevant work experience.",
    )
    assert (junior.years_min, junior.years_max, junior.seniority) == (1.0, 2.0, "junior")
    # Executive titles are above staff level; bank-style "VP" on an IC role is not.
    assert analyze_job("Vice President, Data Engineering", "").seniority == "executive"
    assert analyze_job("Director, Software Engineering", "").seniority == "executive"
    assert analyze_job("Engineering Manager, Data Platform", "").seniority == "manager"
    assert analyze_job("Data Engineer - Vice President", "").seniority == "staff"
    assert analyze_job("Senior Data Engineer - AVP", "").seniority == "senior"


def test_jd_sections_with_uncommon_headings_are_classified() -> None:
    description = "\n".join(
        [
            "Our Purpose",
            "We power payments in 200 countries.",
            "Role",
            "In this position, you will:",
            "- Design, develop, and support data pipelines on cloud data platforms.",
            "- Monitor and improve the reliability of production data processes.",
            "All About You",
            "The ideal candidate for this position should:",
            "- Possess strong SQL and data modeling skills.",
            "- Be familiar with tools such as Databricks, Snowflake, dbt, Spark.",
            "Overview",
            "- Build streaming pipelines with Kafka.",
            "Corporate Security Responsibility",
            "- Complete all periodic mandatory security trainings with Python.",
        ]
    )
    analysis = analyze_job("Data Engineer II", description)
    kinds = {item["text"]: item["kind"] for item in analysis.requirements}
    assert kinds["Design, develop, and support data pipelines on cloud data platforms."] == (
        "responsibility"
    )
    assert kinds["Possess strong SQL and data modeling skills."] == "required"
    assert kinds["Be familiar with tools such as Databricks, Snowflake, dbt, Spark."] == "required"
    # Unknown heading: the opening verb marks a responsibility.
    assert kinds["Build streaming pipelines with Kafka."] == "responsibility"
    assert not any("security trainings" in text for text in kinds)
    assert {"SQL", "Data modeling", "Spark"} <= {s["name"] for s in analysis.required_skills}

    _parsed, _facts, _person, model = model_for()
    match = match_job(
        model,
        analysis.to_dict(),
        title="Data Engineer II",
        location="Pune, India",
        role_category="data_engineering",
        role_relevance=0.95,
    )
    importance = {item["name"]: item["importance"] for item in match.skill_assessments}
    # Databricks/Spark satisfy "Databricks, Snowflake, dbt, Spark", so Snowflake is optional.
    assert importance["Snowflake"] == "alternative" and importance["dbt"] == "alternative"
    assert not any("Snowflake" in line for line in match.explanation if "Required" in line)
    assert any(line.startswith("Not a gap") for line in match.explanation)


def test_matching_explains_strong_transferable_project_and_gap_evidence() -> None:
    _parsed, _facts, _person, model = model_for()
    analysis = analyze_job(
        "Senior Data Engineer", STREAMING_JD, location="Bengaluru, India"
    ).to_dict()
    match = match_job(
        model,
        analysis,
        title="Senior Data Engineer",
        location="Bengaluru, India",
        role_category="data_engineering",
        role_relevance=0.95,
    )
    status = {item["name"]: item["status"] for item in match.skill_assessments}
    assert status["Kafka"] == "STRONG" and status["Spark"] == "STRONG"
    assert status["AWS"] == "TRANSFERABLE"  # Azure experience, explicitly not AWS
    assert status["Airflow"] == "PROJECT" and status["Kubernetes"] == "GAP"
    assert match.location_fit["status"] == "MATCH" and match.seniority_fit == "STRONG_MATCH"
    assert any("preferred" in line or "Required skills" in line for line in match.explanation)
    assert 60 <= match.fit_score <= 100

    seattle = match_job(
        model,
        analysis,
        title="Senior Data Engineer",
        location="Seattle, WA, USA",
        role_category="data_engineering",
        role_relevance=0.95,
    )
    assert seattle.location_fit["status"] == "MISMATCH"
    senior = analyze_job("Principal Data Engineer", SENIOR_JD).to_dict()
    stretch = match_job(
        model,
        senior,
        title="Principal Data Engineer",
        location="Mumbai, India",
        role_category="data_engineering",
        role_relevance=0.95,
    )
    assert stretch.seniority_fit == "UNDERQUALIFIED"


def test_priority_gates_and_classes() -> None:
    _parsed, _facts, _person, model = model_for()
    analysis = analyze_job("Senior Data Engineer", STREAMING_JD, location="Mumbai, India").to_dict()
    good = match_job(
        model,
        analysis,
        title="Senior Data Engineer",
        location="Mumbai, India",
        role_category="data_engineering",
        role_relevance=0.95,
    )
    fresh, note = freshness_score(6)
    kwargs = dict(
        company_target=85.0,
        company_career_growth=90.0,
        freshness=fresh,
        freshness_note=note,
        age_hours=6,
        role_is_target=True,
        listing_open=True,
        ats="greenhouse",
        tailored_resume_ready=True,
    )
    priority = application_priority(match=good, **kwargs)
    assert priority.priority_class in {"P0", "P1"} and priority.recommendation.startswith("Apply")
    far = match_job(
        model,
        analysis,
        title="Senior Data Engineer",
        location="Seattle, WA, USA",
        role_category="data_engineering",
        role_relevance=0.95,
    )
    rejected = application_priority(match=far, **kwargs)
    assert rejected.priority_class == "REJECT" and rejected.recommendation.startswith(
        "Skip: Location"
    )
    unknown_date = application_priority(match=good, **{**kwargs, "age_hours": None})
    assert unknown_date.priority_class in {
        "P2",
        "P3",
        "REJECT",
    }  # undated jobs are never top priority


def _tailored(jd: str = STREAMING_JD, company: str = "Mastercard"):
    _parsed, facts, person, model = model_for()
    analysis = analyze_job("Senior Data Engineer", jd, location="Mumbai, India").to_dict()
    match = match_job(
        model,
        analysis,
        title="Senior Data Engineer",
        location="Mumbai, India",
        role_category="data_engineering",
        role_relevance=0.95,
    )
    base = build_resume_base(facts, ["summary", "experience", "skills", "projects", "education"])
    tailored = tailor_resume(
        candidate=person,
        model=model,
        base=base,
        facts_by_id={fact.id: fact for fact in facts},
        analysis=analysis,
        match=match,
        positioning=positioning_for(company, jd_context=jd),
        jd_text=jd,
    )
    return facts, person, model, base, analysis, match, tailored


def test_tailoring_reorders_and_emphasizes_without_inventing() -> None:
    facts, person, model, base, analysis, match, tailored = _tailored()
    claims = tailored.claims
    assert all(claim.verified for claim in claims if claim.included), [
        (claim.text, claim.notes) for claim in claims if not claim.verified
    ]
    experience = [
        claim for claim in claims if claim.section == "experience" and claim.entry_key == "exp-0"
    ]
    first = min(experience, key=lambda claim: claim.position)
    assert first.label == "Streaming Ingestion"  # Kafka/streaming bullet leads for a streaming JD
    reporting = next(claim for claim in experience if claim.label == "Reporting Support")
    assert reporting.position == len(experience) - 1  # the Power BI bullet moves to the end
    headline = claims[tailored.content["headline"]].text
    assert headline.startswith("Senior Data Engineer |") and "Kafka" in headline
    summary = claims[tailored.content["summary"][0]].text
    assert "7+ years" in summary and "AWS" not in summary and "Kubernetes" not in summary
    rendered = to_text(tailored.content, lambda ref: claims[ref] if isinstance(ref, int) else None)
    fact_techs = {name for fact in facts for name in fact.technologies} | {
        hit.name for fact in facts for hit in find_terms(fact.statement, kinds=("tech",))
    }
    rendered_techs = {hit.name for hit in find_terms(rendered, kinds=("tech",))}
    assert rendered_techs <= fact_techs | {"Spark", "Azure"}, rendered_techs - fact_techs
    assert "Kubernetes" not in rendered and "Iceberg" not in rendered
    assert tailored.changes["summary_changed"] and tailored.changes["skills_reordered"]
    assert tailored.changes["unsupported_claims"] == 0
    # A batch-processing JD reorders the same genuine bullets differently.
    batch_jd = (
        "Basic qualifications\n- Spark performance tuning for large batch workloads\n"
        "- Hadoop and Hive experience\n- Strong SQL\n"
    )
    batch = _tailored(batch_jd)[-1]
    first_batch = min(
        (
            claim
            for claim in batch.claims
            if claim.section == "experience" and claim.entry_key == "exp-0"
        ),
        key=lambda claim: claim.position,
    )
    assert first_batch.label == "Batch Optimization"
    assert any(
        item["label"] == "Batch Optimization" and item["direction"] == "up"
        for item in batch.changes["reordered"]
    )
    master = master_resume(candidate=person, base=base)
    baseline = to_text(
        master.content, lambda ref: master.claims[ref] if isinstance(ref, int) else None
    )
    quality = evaluate_resume(
        claims,
        analysis=analysis,
        match=match,
        rendered_text=rendered,
        candidate_titles=model.titles,
        years_experience=model.years,
        baseline_text=baseline,
    )
    assert (
        quality["truth_validation"]["passed"]
        and quality["alignment_breakdown"]["truth_confidence"] == 100
    )
    checks = {check["check"]: check["status"] for check in quality["quality_checks"]}
    assert checks["truth"] == "pass" and checks["seniority"] == "pass" and checks["ats"] == "pass"


def test_claim_verifier_rejects_inflated_or_invented_statements() -> None:
    _parsed, facts = resume_facts()
    kafka = next(fact for fact in facts if fact.label == "Streaming Ingestion")
    project = next(fact for fact in facts if fact.category == "project")
    assert verify_claim(kafka.statement, [kafka], kind="verbatim").ok
    assert not verify_claim(kafka.statement.replace("Kafka", "Kafka and AWS Kinesis"), [kafka]).ok
    assert not verify_claim(kafka.statement.replace("35%", "50%"), [kafka]).ok
    led = verify_claim("Led a team building Kafka-based streaming ingestion.", [kafka])
    assert not led.ok and any("leadership" in note for note in led.notes)
    production = verify_claim(
        "Built a production Airflow pipeline loading Delta Lake tables.", [project]
    )
    assert not production.ok
    copied = verify_claim(
        "Design and operate streaming data pipelines on Kafka and Spark Structured Streaming daily.",
        [kafka],
        jd_text=STREAMING_JD,
    )
    assert not copied.ok
    assert not verify_claim(
        "Senior Staff Data Engineer with Kafka", [kafka], candidate_titles=["Data Engineer"]
    ).ok


async def test_ai_rewrites_are_parsed_and_unsupported_ones_fail_verification() -> None:
    _parsed, facts = resume_facts()
    kafka = next(fact for fact in facts if fact.label == "Streaming Ingestion")
    proposals = {
        "a": "Built Kafka streaming ingestion with exponential backoff for event-driven consumers, "
        "cutting consumer lag by 35% across 12 topics.",
        "b": "Built Kafka and Kubernetes streaming ingestion, cutting consumer lag by 60%.",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert "jane.doe@example.com" not in request.content.decode()  # no contact data is sent
        assert payload["model"] == "test-model"
        content = json.dumps(
            {"rewrites": [{"id": key, "text": text} for key, text in proposals.items()]}
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    settings = Settings(llm_provider="openai", llm_api_key="test", llm_model="test-model")
    rewriter = LLMRewriter(settings, transport=httpx.MockTransport(handler))
    assert rewriter.configured
    result = await rewriter.rewrite(
        [RewriteRequest("a", kafka.statement, ["Kafka"]), RewriteRequest("b", kafka.statement, [])],
        jd_terms=["Kafka"],
        company="Acme",
        role="Data Engineer",
    )
    assert verify_claim(result["a"], [kafka], max_length_ratio=1.35).ok
    rejected = verify_claim(result["b"], [kafka], max_length_ratio=1.35)
    assert not rejected.ok and "Kubernetes" in rejected.unsupported_terms


def test_docx_and_markdown_rendering_are_ats_friendly() -> None:
    _facts, _person, _model, _base, _analysis, _match, tailored = _tailored()
    resolve = lambda ref: tailored.claims[ref] if isinstance(ref, int) else None  # noqa: E731
    markdown = to_markdown(tailored.content, resolve)
    assert markdown.startswith("# Jane Doe") and "## Professional Experience" in markdown
    assert "<table" not in markdown and "<img" not in markdown
    archive = zipfile.ZipFile(io.BytesIO(to_docx(tailored.content, resolve)))
    document = archive.read("word/document.xml").decode()
    assert "Jane Doe" in document and "PROFESSIONAL EXPERIENCE" in document
    assert "w:tbl" not in document  # no tables


def test_company_positioning_uses_signals_not_new_experience() -> None:
    amazon = positioning_for("Amazon")
    assert "ownership" in amazon["signals"] and amazon["term_boosts"]["Distributed systems"] > 0
    fintech = positioning_for("Acme Pay", jd_context="We build card payments infrastructure")
    assert fintech["archetype"] == "fintech"
    unknown = positioning_for("Quiet Corp")
    assert unknown["archetype"] is None and unknown["signals"] == []


def test_calibration_guards_against_non_data_roles_and_false_matches() -> None:
    from app.discovery.roles import classify_role

    assert (
        classify_role("Product Delivery Manager - Account & Reference Data Platform").category
        == "non_target"
    )
    assert not classify_role("Software Engineer III - Salesforce Platform Developer").is_target
    assert classify_role("Engineering Manager, Data Platform").is_target
    assert "Spark" not in {hit.name for hit in find_terms("Strong C and/or Ada/SPARK programming")}
    assert "Leadership & mentoring" in {
        hit.name
        for hit in find_terms(
            "Proven experience leading global data engineering or platform engineering teams"
        )
    }
    _parsed, _facts, _person, model = model_for()
    analysis = analyze_job("Senior Data Engineer", STREAMING_JD, location="Mumbai, India").to_dict()
    good = match_job(
        model,
        analysis,
        title="Senior Data Engineer",
        location="Mumbai, India",
        role_category="data_engineering",
        role_relevance=0.95,
    )
    weak = type(good)(**{**good.__dict__, "fit_score": 60.0})
    kwargs = dict(
        company_target=95.0,
        company_career_growth=100.0,
        freshness=100.0,
        freshness_note="fresh",
        age_hours=4,
        role_is_target=True,
        listing_open=True,
        ats="greenhouse",
        tailored_resume_ready=True,
    )
    assert application_priority(match=weak, **kwargs).priority_class == "P2"  # P1 needs fit >= 65
    manager = type(good)(
        **{
            **good.__dict__,
            "component_notes": {
                **good.component_notes,
                "seniority": "This is a people-management title; your history is individual-contributor.",
            },
        }
    )
    capped = application_priority(match=manager, **kwargs)
    assert capped.priority_class == "P2" and any(
        "people-management" in reason for reason in capped.reasons
    )


def test_current_employer_jobs_are_capped_at_p3() -> None:
    from app.intelligence.targeting import is_current_employer

    assert is_current_employer("Accenture", "Accenture")
    assert is_current_employer("Accenture Solutions Pvt Ltd", "Accenture")
    assert is_current_employer("JP Morgan", "JPMorgan")
    assert not is_current_employer("Metabase", "Meta")
    assert not is_current_employer("Accenture", "Capgemini")
    assert not is_current_employer(None, "Accenture")

    _parsed, _facts, _person, model = model_for()
    analysis = analyze_job("Senior Data Engineer", STREAMING_JD, location="Mumbai, India").to_dict()
    good = match_job(
        model,
        analysis,
        title="Senior Data Engineer",
        location="Mumbai, India",
        role_category="data_engineering",
        role_relevance=0.95,
    )
    kwargs = dict(
        match=good,
        company_target=95.0,
        company_career_growth=100.0,
        freshness=100.0,
        freshness_note="fresh",
        age_hours=4,
        role_is_target=True,
        listing_open=True,
        ats="greenhouse",
        tailored_resume_ready=True,
    )
    external = application_priority(**kwargs)
    internal = application_priority(**kwargs, current_employer=True)
    assert external.priority_class in {"P0", "P1"}
    assert internal.priority_class == "P3"
    assert internal.recommendation.startswith("Your current employer")
    assert [reason for reason in internal.reasons if reason.startswith("Capped")] == [
        "Capped at P3: this is your current employer; an internal move is usually a better "
        "route than an external application."
    ]
    junior = type(good)(**{**good.__dict__, "seniority_fit": "OVERQUALIFIED"})
    down_level = application_priority(**{**kwargs, "match": junior})
    assert down_level.priority_class == "P2"
    assert any("below your experience level" in reason for reason in down_level.reasons)

    from app.intelligence.targeting import CompanyContext, company_target_score

    def target(current_employer: bool) -> tuple[float, list[str]]:
        context = CompanyContext(
            name="Acme Data",
            tier="S",
            discovery_breakdown={},
            relevant_open_jobs=4,
            fresh_relevant_jobs=3,
            india_presence=True,
            remote_presence=None,
            company_type="product",
            industry=None,
            job_fits=[90.0, 88.0],
            current_employer=current_employer,
        )
        score, _breakdown, reasons = company_target_score(context, model)
        return score, reasons

    assert target(False)[0] > 35.0
    capped_score, capped_reasons = target(True)
    assert capped_score <= 35.0 and capped_reasons[0].startswith("Your current employer")


def test_specialised_titles_and_employer_names_are_not_data_signals() -> None:
    from app.discovery.roles import classify_role

    for title in (
        "Senior Network Infrastructure Engineer",
        "Senior System Software Engineer - Halos Core and Robotics Platform",
        "Staff Software Engineer, Frontier Security Team",
        "Software Engineer - Microsoft Power Platform Developer",
    ):
        assert not classify_role(title).is_target, title
    assert classify_role("Data Platform Engineer, Security").is_target
    assert classify_role("Infrastructure Engineer II - Devops").is_target
    description = (
        "Snowflake builds the AI Data Cloud. Join Snowflake to run Kubernetes services. " * 4
    )
    assert "Snowflake" in classify_role("Software Engineer", description).signals
    assert "Snowflake" not in classify_role("Software Engineer", description, "Snowflake").signals


def test_workday_hosts_never_become_company_names() -> None:
    from app.discovery.extraction import clean_company_candidate, workday_company_name

    assert clean_company_candidate("rockwellautomation.wd1.myworkdayjobs.com") is None
    assert clean_company_candidate("boards.greenhouse.io") is None
    assert workday_company_name("rockwellautomation", "External_Rockwell_Automation") == (
        "Rockwell Automation"
    )
    assert workday_company_name("nvidia", "NVIDIAExternalCareerSite") == "NVIDIA"
    assert workday_company_name("kyndryl", "kyndrylprofessionalcareers") == "Kyndryl"
    assert workday_company_name("nxp", "careers") == "NXP"


def test_todays_plan_skips_duplicate_postings_and_prefers_company_variety() -> None:
    import uuid
    from types import SimpleNamespace

    from app.api.routes.opportunities import _plan_rows

    acme, globex = uuid.uuid4(), uuid.uuid4()

    def row(company_id, title, priority_class, score):
        job = SimpleNamespace(id=uuid.uuid4(), company_id=company_id, company="", title=title)
        return (SimpleNamespace(priority_class=priority_class, priority_score=score), job, None)

    rows = [
        row(acme, "Senior Data Engineer", "P0", 90),
        row(acme, "Senior Data Engineer-1", "P1", 85),  # re-post of the same role
        row(acme, "Lead Data Engineer", "P1", 84),
        row(globex, "Data Engineer", "P1", 80),
        row(globex, "Data Engineer", "P2", 79),  # same title, another city
    ]
    chosen = [(job.company_id, job.title) for _score, job, _company in _plan_rows(rows)]
    assert chosen == [
        (acme, "Senior Data Engineer"),
        (globex, "Data Engineer"),
        (acme, "Lead Data Engineer"),
    ]
