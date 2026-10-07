"""phase two company universe and discovery

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("name_key", sa.String(200), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("legal_name", sa.String(300)),
        sa.Column("domain", sa.String(255)),
        sa.Column("careers_url", sa.Text()),
        sa.Column("linkedin_url", sa.Text()),
        sa.Column("x_url", sa.Text()),
        sa.Column("industry", sa.String(200)),
        sa.Column("company_type", sa.String(100)),
        sa.Column("company_stage", sa.String(100)),
        sa.Column("headquarters", sa.String(200)),
        sa.Column("india_presence", sa.Boolean()),
        sa.Column("remote_presence", sa.Boolean()),
        sa.Column("employee_range", sa.String(50)),
        sa.Column("funding_stage", sa.String(100)),
        sa.Column("funding", sa.String(200)),
        sa.Column("known_technologies", sa.JSON(), nullable=False),
        sa.Column("data_infrastructure_signals", sa.JSON(), nullable=False),
        sa.Column("engineering_signals", sa.JSON(), nullable=False),
        sa.Column("tier", sa.String(40), nullable=False, server_default="UNCATEGORIZED"),
        sa.Column("status", sa.String(20), nullable=False, server_default="DISCOVERED"),
        sa.Column("review_status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("is_seed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("discovery_source", sa.String(60), nullable=False, server_default="UNKNOWN"),
        sa.Column("discovery_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("discovery_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("score_breakdown", sa.JSON(), nullable=False),
        sa.Column("score_reasons", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("last_scanned_at", sa.DateTime(timezone=True)),
        sa.Column("last_explored_at", sa.DateTime(timezone=True)),
        *_timestamps(),
    )
    op.create_index("ix_companies_name_key", "companies", ["name_key"], unique=True)
    op.create_index("ix_companies_domain", "companies", ["domain"])
    op.create_index("ix_companies_status", "companies", ["status"])

    op.create_table(
        "discovery_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="QUEUED"),
        sa.Column("trigger", sa.String(30), nullable=False, server_default="manual"),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("budgets", sa.JSON(), nullable=False),
        sa.Column("progress", sa.JSON(), nullable=False),
        sa.Column("dedupe_key", sa.String(200)),
        sa.Column("companies_discovered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("jobs_inspected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("jobs_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text()),
        sa.Column("errors", sa.JSON(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("lease_owner", sa.String(100)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_discovery_runs_kind", "discovery_runs", ["kind"])
    op.create_index("ix_discovery_runs_status", "discovery_runs", ["status"])
    op.create_index("ix_discovery_runs_dedupe_key", "discovery_runs", ["dedupe_key"])

    op.create_table(
        "career_sources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "company_id",
            sa.Uuid(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("platform", sa.String(40), nullable=False, server_default="unknown"),
        sa.Column("platform_confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("platform_identifier", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("discovered_via", sa.String(60), nullable=False, server_default="UNKNOWN"),
        sa.Column("evidence", sa.Text()),
        sa.Column("scan_status", sa.String(30), nullable=False, server_default="PENDING"),
        sa.Column("error", sa.Text()),
        sa.Column("last_scanned_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("last_complete_scan_at", sa.DateTime(timezone=True)),
        sa.Column("jobs_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("relevant_jobs_found", sa.Integer(), nullable=False, server_default="0"),
        *_timestamps(),
        sa.UniqueConstraint("company_id", "url", name="uq_career_source_company_url"),
    )
    op.create_index("ix_career_sources_company_id", "career_sources", ["company_id"])

    op.create_table(
        "company_discovery_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "company_id",
            sa.Uuid(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("discovery_runs.id", ondelete="SET NULL")),
        sa.Column("fingerprint", sa.String(64), nullable=False, unique=True),
        sa.Column("source", sa.String(60), nullable=False),
        sa.Column("source_url", sa.Text()),
        sa.Column("reason", sa.String(40), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("evidence_data", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("observation_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_company_discovery_events_company_id", "company_discovery_events", ["company_id"]
    )
    op.create_index("ix_company_discovery_events_reason", "company_discovery_events", ["reason"])
    op.create_index("ix_company_discovery_events_status", "company_discovery_events", ["status"])

    op.create_table(
        "discovery_candidates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "company_id",
            sa.Uuid(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("priority", sa.Float(), nullable=False, server_default="0"),
        sa.Column("priority_breakdown", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("depth", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "source_run_id", sa.Uuid(), sa.ForeignKey("discovery_runs.id", ondelete="SET NULL")
        ),
        sa.Column(
            "explored_run_id", sa.Uuid(), sa.ForeignKey("discovery_runs.id", ondelete="SET NULL")
        ),
        sa.Column("error", sa.Text()),
        *_timestamps(),
    )
    op.create_index("ix_discovery_candidates_company_id", "discovery_candidates", ["company_id"])
    op.create_index("ix_discovery_candidates_status", "discovery_candidates", ["status"])

    op.create_table(
        "company_relationships",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "source_company_id",
            sa.Uuid(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_company_id",
            sa.Uuid(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relationship_type", sa.String(40), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("evidence_url", sa.Text()),
        *_timestamps(),
        sa.UniqueConstraint(
            "source_company_id",
            "target_company_id",
            "relationship_type",
            name="uq_company_relationship",
        ),
    )
    op.create_index(
        "ix_company_relationships_source_company_id",
        "company_relationships",
        ["source_company_id"],
    )
    op.create_index(
        "ix_company_relationships_target_company_id",
        "company_relationships",
        ["target_company_id"],
    )

    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("company_id", sa.Uuid(), nullable=True))
        batch.add_column(
            sa.Column("role_category", sa.String(40), nullable=False, server_default="unclassified")
        )
        batch.add_column(
            sa.Column("role_relevance", sa.Float(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("posted_at_precision", sa.String(20)))
        batch.add_column(sa.Column("source_updated_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("last_seen_at", sa.DateTime(timezone=True)))
        batch.add_column(
            sa.Column("listing_status", sa.String(20), nullable=False, server_default="OPEN")
        )
        batch.add_column(sa.Column("closed_at", sa.DateTime(timezone=True)))
        batch.create_foreign_key(
            "fk_jobs_company_id", "companies", ["company_id"], ["id"], ondelete="SET NULL"
        )
        batch.create_index("ix_jobs_company_id", ["company_id"])
        batch.create_index("ix_jobs_role_category", ["role_category"])

    with op.batch_alter_table("job_sources") as batch:
        batch.add_column(sa.Column("career_source_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("last_seen_at", sa.DateTime(timezone=True)))
        batch.add_column(
            sa.Column("listing_status", sa.String(20), nullable=False, server_default="OPEN")
        )
        batch.add_column(sa.Column("closed_at", sa.DateTime(timezone=True)))
        batch.create_foreign_key(
            "fk_job_sources_career_source_id",
            "career_sources",
            ["career_source_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_job_sources_career_source_id", ["career_source_id"])


def downgrade() -> None:
    with op.batch_alter_table("job_sources") as batch:
        batch.drop_index("ix_job_sources_career_source_id")
        batch.drop_constraint("fk_job_sources_career_source_id", type_="foreignkey")
        for column in ("closed_at", "listing_status", "last_seen_at", "career_source_id"):
            batch.drop_column(column)
    with op.batch_alter_table("jobs") as batch:
        batch.drop_index("ix_jobs_role_category")
        batch.drop_index("ix_jobs_company_id")
        batch.drop_constraint("fk_jobs_company_id", type_="foreignkey")
        for column in (
            "closed_at",
            "listing_status",
            "last_seen_at",
            "source_updated_at",
            "posted_at_precision",
            "role_relevance",
            "role_category",
            "company_id",
        ):
            batch.drop_column(column)
    op.drop_table("app_settings")
    op.drop_table("company_relationships")
    op.drop_table("discovery_candidates")
    op.drop_table("company_discovery_events")
    op.drop_table("career_sources")
    op.drop_table("discovery_runs")
    op.drop_table("companies")
