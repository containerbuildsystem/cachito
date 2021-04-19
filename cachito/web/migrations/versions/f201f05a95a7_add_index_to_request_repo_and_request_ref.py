"""Add index to Request.repo and Request.ref

Revision ID: f201f05a95a7
Revises: 07d89c5778f2
Create Date: 2021-04-21 22:12:36.880084

"""
from alembic import op
import sqlalchemy as sa


revision = "f201f05a95a7"
down_revision = "07d89c5778f2"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("request", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_request_repo"), ["repo"], unique=False)
        batch_op.create_index(batch_op.f("ix_request_ref"), ["ref"], unique=False)


def downgrade():
    with op.batch_alter_table("request", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_request_ref"))
        batch_op.drop_index(batch_op.f("ix_request_repo"))
