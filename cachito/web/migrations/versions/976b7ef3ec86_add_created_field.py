"""Add created field to Request table

Revision ID: 976b7ef3ec86
Revises: 491454f79a8b
Create Date: 2021-10-01 05:17:37.099131

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "976b7ef3ec86"
down_revision = "491454f79a8b"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("request", schema=None) as batch_op:
        batch_op.add_column(sa.Column("created", sa.DateTime(), nullable=True))
        batch_op.create_index(batch_op.f("ix_request_created"), ["created"], unique=False)


def downgrade():
    with op.batch_alter_table("request", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_request_created"))
        batch_op.drop_column("created")
