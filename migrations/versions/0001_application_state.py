"""Single-user connections, project configuration, durable evaluations and bundle links."""

import sqlalchemy as sa
from alembic import op

revision = "0001_application_state"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider", sa.String(16), nullable=False, unique=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("configuration", sa.Text(), nullable=False),
        sa.Column("encrypted_credential", sa.Text(), nullable=False),
        sa.Column("validation_status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "project_configs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("jira_project", sa.String(64), nullable=False),
        sa.Column("github_repository", sa.String(255), nullable=False),
        sa.Column(
            "jira_connection_id", sa.String(36), sa.ForeignKey("connections.id"), nullable=False
        ),
        sa.Column(
            "github_connection_id", sa.String(36), sa.ForeignKey("connections.id"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "evaluation_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("project_configs.id"), nullable=False),
        sa.Column("repository", sa.String(255), nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=False),
        sa.Column("base_sha", sa.String(64)),
        sa.Column("head_sha", sa.String(64)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("stage", sa.String(40), nullable=False),
        sa.Column("active_key", sa.String(400), unique=True),
        sa.Column("idempotency_key", sa.String(200), unique=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("safe_error_code", sa.String(64)),
        sa.Column("safe_error_message", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_jobs_claim", "evaluation_jobs", ["status", "created_at"])
    op.create_index("ix_jobs_active_pr", "evaluation_jobs", ["active_key"], unique=True)
    op.create_table(
        "evaluation_stage_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("evaluation_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage", sa.String(40), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("message", sa.String(300), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "evaluation_bundles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "evaluation_id",
            sa.String(36),
            sa.ForeignKey("evaluation_jobs.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("bundle_path", sa.Text(), nullable=False),
        sa.Column("bundle_hash", sa.String(64), nullable=False),
        sa.Column("verdict", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_evaluation_bundles_bundle_hash", "evaluation_bundles", ["bundle_hash"])


def downgrade() -> None:
    op.drop_table("evaluation_bundles")
    op.drop_table("evaluation_stage_events")
    op.drop_index("ix_jobs_active_pr", table_name="evaluation_jobs")
    op.drop_index("ix_jobs_claim", table_name="evaluation_jobs")
    op.drop_table("evaluation_jobs")
    op.drop_table("project_configs")
    op.drop_table("connections")
