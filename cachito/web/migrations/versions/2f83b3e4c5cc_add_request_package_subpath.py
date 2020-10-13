"""Add the subpath column to the request_package table

Revision ID: 2f83b3e4c5cc
Revises: f133002ffdb4
Create Date: 2020-10-13 11:43:25.052014

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "2f83b3e4c5cc"
down_revision = "f133002ffdb4"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("request_package") as batch_op:
        batch_op.add_column(sa.Column("subpath", sa.String(), nullable=True))


def downgrade():
    with op.batch_alter_table("request_package") as batch_op:
        batch_op.drop_column("subpath")
