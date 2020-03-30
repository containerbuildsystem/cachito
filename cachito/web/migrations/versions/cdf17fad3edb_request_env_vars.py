"""Add support for environment variables in request

Revision ID: cdf17fad3edb
Revises: c8b2a3a26191
Create Date: 2019-09-04 21:07:16.631196

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "cdf17fad3edb"
down_revision = "c8b2a3a26191"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "environment_variable",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("value", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "value"),
    )
    op.create_table(
        "request_environment_variable",
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("env_var_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["env_var_id"], ["environment_variable.id"]),
        sa.ForeignKeyConstraint(["request_id"], ["request.id"]),
        sa.UniqueConstraint("request_id", "env_var_id"),
    )


def downgrade():
    op.drop_table("request_environment_variable")
    op.drop_table("environment_variable")
