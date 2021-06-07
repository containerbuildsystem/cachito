"""Add packages_count and dependencies_count to requests

Revision ID: 491454f79a8b
Revises: 4d17dec0cfc3
Create Date: 2021-06-03 18:01:55.976908

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "491454f79a8b"
down_revision = "4d17dec0cfc3"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("request") as batch_op:
        batch_op.add_column(sa.Column("packages_count", sa.Integer()))
        batch_op.add_column(sa.Column("dependencies_count", sa.Integer()))


def downgrade():
    with op.batch_alter_table("request") as batch_op:
        batch_op.drop_column("packages_count")
        batch_op.drop_column("dependencies_count")
