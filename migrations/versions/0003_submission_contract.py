"""Persist submission-time evaluation configuration; legacy jobs remain explicit."""

import sqlalchemy as sa
from alembic import op

revision = "0003_submission_contract"
down_revision = "0002_request_fingerprint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("evaluation_jobs", sa.Column("submission_contract", sa.Text()))


def downgrade() -> None:
    op.drop_column("evaluation_jobs", "submission_contract")
