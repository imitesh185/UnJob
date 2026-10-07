"""phase one canonical jobs

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("company", sa.String(200), nullable=False, index=True),
        sa.Column("title", sa.String(300), nullable=False, index=True),
        sa.Column("normalized_title", sa.String(300), nullable=False, index=True),
        sa.Column("location", sa.String(300), nullable=False, server_default="Unknown"),
        sa.Column("remote_status", sa.String(30), nullable=False, server_default="unknown"),
        sa.Column("salary", sa.String(300)),
        sa.Column("employment_type", sa.String(100)),
        sa.Column("ats", sa.String(50), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True)),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("requirements", sa.JSON(), nullable=False),
        sa.Column("preferred_requirements", sa.JSON(), nullable=False),
        sa.Column("technology_stack", sa.JSON(), nullable=False),
        sa.Column("experience_requirement", sa.String(200)),
        sa.Column("application_url", sa.Text(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="Discovered"),
        sa.Column("overall_score", sa.Float(), nullable=False),
        sa.Column("score_breakdown", sa.JSON(), nullable=False),
        sa.Column("score_reasons", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "job_sources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "job_id", sa.Uuid(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("external_id", sa.String(200), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source", "external_id", name="uq_job_source_identity"),
    )
    op.create_index("ix_job_sources_job_id", "job_sources", ["job_id"])


def downgrade() -> None:
    op.drop_table("job_sources")
    op.drop_table("jobs")
