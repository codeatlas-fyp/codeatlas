"""Record canonical request identity without inventing identity for legacy jobs."""

import sqlalchemy as sa
from alembic import op

revision = "0002_request_fingerprint"
down_revision = "0001_application_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("evaluation_jobs", sa.Column("request_fingerprint", sa.String(64)))


def downgrade() -> None:
    op.drop_column("evaluation_jobs", "request_fingerprint")
