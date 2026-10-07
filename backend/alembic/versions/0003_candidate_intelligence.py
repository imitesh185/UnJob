"""phase three candidate intelligence, tailoring and applications

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _created() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False)


def _updated() -> sa.Column:
    return sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)


def _fk(table: str, ondelete: str) -> sa.ForeignKey:
    return sa.ForeignKey(f"{table}.id", ondelete=ondelete)


def upgrade() -> None:
    with op.batch_alter_table("companies") as batch:
        batch.add_column(
            sa.Column("tier_source", sa.String(20), nullable=False, server_default="default")
        )
        batch.add_column(sa.Column("archetype", sa.String(40)))
        batch.add_column(sa.Column("positioning", sa.JSON(), nullable=False, server_default="{}"))

    op.create_table(
        "candidates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("full_name", sa.String(200)),
        sa.Column("email", sa.String(200)),
        sa.Column("phone", sa.String(60)),
        sa.Column("links", sa.JSON(), nullable=False),
        sa.Column("headline", sa.String(300)),
        sa.Column("current_title", sa.String(200)),
        sa.Column("current_company", sa.String(200)),
        sa.Column("location", sa.String(200)),
        sa.Column("years_experience", sa.Float()),
        sa.Column("years_experience_source", sa.String(200)),
        sa.Column("target_roles", sa.JSON(), nullable=False),
        sa.Column("preferred_locations", sa.JSON(), nullable=False),
        sa.Column("remote_preference", sa.String(30)),
        sa.Column("open_to_relocation", sa.Boolean()),
        sa.Column("compensation_target", sa.String(100)),
        sa.Column("compensation_min", sa.Float()),
        sa.Column("compensation_currency", sa.String(10)),
        sa.Column("notice_period", sa.String(100)),
        sa.Column("domains", sa.JSON(), nullable=False),
        sa.Column("preference_sources", sa.JSON(), nullable=False),
        sa.Column("active_resume_id", sa.Uuid()),
        sa.Column("profile_version", sa.Integer(), nullable=False, server_default="1"),
        _created(),
        _updated(),
    )

    op.create_table(
        "resumes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("candidate_id", sa.Uuid(), _fk("candidates", "CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("file_path", sa.Text()),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("parsed", sa.JSON(), nullable=False),
        sa.Column("parser", sa.String(60), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        _created(),
    )
    op.create_index("ix_resumes_candidate_id", "resumes", ["candidate_id"])
    op.create_index("ix_resumes_file_hash", "resumes", ["file_hash"])

    op.create_table(
        "candidate_facts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("candidate_id", sa.Uuid(), _fk("candidates", "CASCADE"), nullable=False),
        sa.Column("resume_id", sa.Uuid(), _fk("resumes", "SET NULL")),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("source", sa.String(60), nullable=False),
        sa.Column("source_ref", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1"),
        sa.Column("experience_type", sa.String(20), nullable=False, server_default="UNKNOWN"),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("technologies", sa.JSON(), nullable=False),
        sa.Column("concepts", sa.JSON(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("employer", sa.String(200)),
        sa.Column("role_title", sa.String(200)),
        sa.Column("label", sa.String(200)),
        sa.Column("start_date", sa.String(10)),
        sa.Column("end_date", sa.String(10)),
        sa.Column("skill_name", sa.String(100)),
        sa.Column("skill_level", sa.String(20)),
        sa.Column("level_source", sa.String(20)),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        _created(),
        _updated(),
    )
    op.create_index("ix_candidate_facts_candidate_id", "candidate_facts", ["candidate_id"])
    op.create_index("ix_candidate_facts_resume_id", "candidate_facts", ["resume_id"])
    op.create_index("ix_candidate_facts_category", "candidate_facts", ["category"])
    op.create_index("ix_candidate_facts_skill_name", "candidate_facts", ["skill_name"])

    op.create_table(
        "job_analyses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(), _fk("jobs", "CASCADE"), nullable=False),
        sa.Column("description_hash", sa.String(64), nullable=False),
        sa.Column("analyzer_version", sa.String(20), nullable=False),
        sa.Column("seniority", sa.String(20), nullable=False, server_default="unknown"),
        sa.Column("years_min", sa.Float()),
        sa.Column("years_max", sa.Float()),
        sa.Column("requirements", sa.JSON(), nullable=False),
        sa.Column("required_skills", sa.JSON(), nullable=False),
        sa.Column("preferred_skills", sa.JSON(), nullable=False),
        sa.Column("responsibilities", sa.JSON(), nullable=False),
        sa.Column("categories", sa.JSON(), nullable=False),
        sa.Column("domains", sa.JSON(), nullable=False),
        sa.Column("leadership_signals", sa.JSON(), nullable=False),
        sa.Column("keywords", sa.JSON(), nullable=False),
        sa.Column("business_context", sa.Text()),
        sa.Column("location_requirements", sa.JSON(), nullable=False),
        sa.Column("compensation", sa.JSON()),
        sa.Column("education", sa.JSON(), nullable=False),
        sa.Column("role_focus", sa.String(30), nullable=False, server_default="unknown"),
        sa.Column("warnings", sa.JSON(), nullable=False),
        _created(),
        _updated(),
    )
    op.create_index("ix_job_analyses_job_id", "job_analyses", ["job_id"], unique=True)

    op.create_table(
        "candidate_job_matches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("candidate_id", sa.Uuid(), _fk("candidates", "CASCADE"), nullable=False),
        sa.Column("job_id", sa.Uuid(), _fk("jobs", "CASCADE"), nullable=False),
        sa.Column("analysis_id", sa.Uuid(), _fk("job_analyses", "SET NULL")),
        sa.Column("profile_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("description_hash", sa.String(64), nullable=False),
        sa.Column("fit_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("breakdown", sa.JSON(), nullable=False),
        sa.Column("seniority_fit", sa.String(20), nullable=False, server_default="UNKNOWN"),
        sa.Column("requirement_matches", sa.JSON(), nullable=False),
        sa.Column("skill_assessments", sa.JSON(), nullable=False),
        sa.Column("strong_matches", sa.JSON(), nullable=False),
        sa.Column("partial_matches", sa.JSON(), nullable=False),
        sa.Column("gaps", sa.JSON(), nullable=False),
        sa.Column("explanation", sa.JSON(), nullable=False),
        sa.Column("location_fit", sa.JSON(), nullable=False),
        sa.Column("compensation_fit", sa.JSON(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("candidate_id", "job_id", name="uq_candidate_job_match"),
    )
    op.create_index(
        "ix_candidate_job_matches_candidate_id", "candidate_job_matches", ["candidate_id"]
    )
    op.create_index("ix_candidate_job_matches_job_id", "candidate_job_matches", ["job_id"])

    op.create_table(
        "target_scores",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("candidate_id", sa.Uuid(), _fk("candidates", "CASCADE"), nullable=False),
        sa.Column("company_id", sa.Uuid(), _fk("companies", "CASCADE"), nullable=False),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("breakdown", sa.JSON(), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("candidate_id", "company_id", name="uq_target_score"),
    )
    op.create_index("ix_target_scores_candidate_id", "target_scores", ["candidate_id"])
    op.create_index("ix_target_scores_company_id", "target_scores", ["company_id"])

    op.create_table(
        "application_scores",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("candidate_id", sa.Uuid(), _fk("candidates", "CASCADE"), nullable=False),
        sa.Column("job_id", sa.Uuid(), _fk("jobs", "CASCADE"), nullable=False),
        sa.Column("company_id", sa.Uuid(), _fk("companies", "SET NULL")),
        sa.Column("fit_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("company_target_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("freshness_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("career_value_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("priority_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("priority_class", sa.String(10), nullable=False, server_default="REJECT"),
        sa.Column("gates", sa.JSON(), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("recommendation", sa.Text(), nullable=False, server_default=""),
        sa.Column("strategy", sa.JSON()),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("candidate_id", "job_id", name="uq_application_score"),
    )
    for column in ("candidate_id", "job_id", "company_id", "priority_score", "priority_class"):
        op.create_index(f"ix_application_scores_{column}", "application_scores", [column])

    op.create_table(
        "resume_variants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("candidate_id", sa.Uuid(), _fk("candidates", "CASCADE"), nullable=False),
        sa.Column("job_id", sa.Uuid(), _fk("jobs", "SET NULL")),
        sa.Column("company_id", sa.Uuid(), _fk("companies", "SET NULL")),
        sa.Column("master_resume_id", sa.Uuid(), _fk("resumes", "SET NULL")),
        sa.Column("master_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("profile_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("jd_hash", sa.String(64), nullable=False),
        sa.Column("jd_snapshot", sa.Text(), nullable=False),
        sa.Column("generator", sa.String(80), nullable=False),
        sa.Column("trigger", sa.String(60), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(20), nullable=False, server_default="USER_REVIEW"),
        sa.Column("positioning", sa.JSON(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("changes", sa.JSON(), nullable=False),
        sa.Column("source_fact_ids", sa.JSON(), nullable=False),
        sa.Column("alignment_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("alignment_breakdown", sa.JSON(), nullable=False),
        sa.Column("truth_validation", sa.JSON(), nullable=False),
        sa.Column("quality_checks", sa.JSON(), nullable=False),
        sa.Column("file_paths", sa.JSON(), nullable=False),
        _created(),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        _updated(),
    )
    for column in ("candidate_id", "job_id", "company_id", "status"):
        op.create_index(f"ix_resume_variants_{column}", "resume_variants", [column])

    op.create_table(
        "resume_claims",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("variant_id", sa.Uuid(), _fk("resume_variants", "CASCADE"), nullable=False),
        sa.Column("section", sa.String(30), nullable=False),
        sa.Column("entry_key", sa.String(200)),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("label", sa.String(200)),
        sa.Column("generated_text", sa.Text(), nullable=False),
        sa.Column("original_text", sa.Text()),
        sa.Column("source_fact_ids", sa.JSON(), nullable=False),
        sa.Column("jd_requirement_ids", sa.JSON(), nullable=False),
        sa.Column("tailoring_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("relevance", sa.Float(), nullable=False, server_default="0"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1"),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("verification_notes", sa.JSON(), nullable=False),
        sa.Column("included", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("rewritten_by", sa.String(80)),
    )
    op.create_index("ix_resume_claims_variant_id", "resume_claims", ["variant_id"])

    op.create_table(
        "applications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("candidate_id", sa.Uuid(), _fk("candidates", "CASCADE"), nullable=False),
        sa.Column("job_id", sa.Uuid(), _fk("jobs", "CASCADE"), nullable=False),
        sa.Column("company_id", sa.Uuid(), _fk("companies", "SET NULL")),
        sa.Column("resume_variant_id", sa.Uuid(), _fk("resume_variants", "SET NULL")),
        sa.Column("status", sa.String(30), nullable=False, server_default="DISCOVERED"),
        sa.Column("source", sa.String(60)),
        sa.Column("priority_class", sa.String(10)),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.Column("recruiter", sa.String(200)),
        sa.Column("hiring_manager", sa.String(200)),
        sa.Column("notes", sa.Text()),
        sa.Column("next_action", sa.String(300)),
        _created(),
        _updated(),
        sa.UniqueConstraint("candidate_id", "job_id", name="uq_application"),
    )
    for column in ("candidate_id", "job_id", "company_id", "status"):
        op.create_index(f"ix_applications_{column}", "applications", [column])

    op.create_table(
        "application_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("application_id", sa.Uuid(), _fk("applications", "CASCADE"), nullable=False),
        sa.Column("from_status", sa.String(30)),
        sa.Column("to_status", sa.String(30), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("actor", sa.String(20), nullable=False, server_default="system"),
        _created(),
    )
    op.create_index(
        "ix_application_events_application_id", "application_events", ["application_id"]
    )

    op.create_table(
        "outreach_drafts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("application_id", sa.Uuid(), _fk("applications", "SET NULL")),
        sa.Column("company_id", sa.Uuid(), _fk("companies", "SET NULL")),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("recipient_name", sa.String(200)),
        sa.Column("recipient_role", sa.String(200)),
        sa.Column("recipient_url", sa.Text()),
        sa.Column("subject", sa.String(300)),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        _created(),
        _updated(),
    )
    op.create_index("ix_outreach_drafts_application_id", "outreach_drafts", ["application_id"])

    op.create_table(
        "search_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("query", sa.String(500), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("snippet", sa.Text(), nullable=False, server_default=""),
        sa.Column("rank", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("run_id", sa.Uuid(), _fk("discovery_runs", "SET NULL")),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_search_results_query", "search_results", ["query"])
    op.create_index("ix_search_results_fetched_at", "search_results", ["fetched_at"])

    op.create_table(
        "provider_health",
        sa.Column("provider", sa.String(40), primary_key=True),
        sa.Column("total_requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("last_failure_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("last_latency_ms", sa.Float()),
        _updated(),
    )


def downgrade() -> None:
    for table in (
        "provider_health",
        "search_results",
        "outreach_drafts",
        "application_events",
        "applications",
        "resume_claims",
        "resume_variants",
        "application_scores",
        "target_scores",
        "candidate_job_matches",
        "job_analyses",
        "candidate_facts",
        "resumes",
        "candidates",
    ):
        op.drop_table(table)
    with op.batch_alter_table("companies") as batch:
        for column in ("positioning", "archetype", "tier_source"):
            batch.drop_column(column)
