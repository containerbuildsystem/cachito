"""Add the config_file_base64 table

Revision ID: 193baf9d7cbf
Revises: 3c208b05d703
Create Date: 2020-04-03 19:16:34.581217
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "193baf9d7cbf"
down_revision = "3c208b05d703"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "config_file_base64",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content", "path"),
    )
    op.create_index(
        op.f("ix_config_file_base64_content"), "config_file_base64", ["content"], unique=False
    )
    op.create_index(
        op.f("ix_config_file_base64_path"), "config_file_base64", ["path"], unique=False
    )

    op.create_table(
        "request_config_file_base64",
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("config_file_base64_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["config_file_base64_id"], ["config_file_base64.id"]),
        sa.ForeignKeyConstraint(["request_id"], ["request.id"]),
        sa.UniqueConstraint("request_id", "config_file_base64_id"),
    )
    op.create_index(
        op.f("ix_request_config_file_base64_config_file_base64_id"),
        "request_config_file_base64",
        ["config_file_base64_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_request_config_file_base64_request_id"),
        "request_config_file_base64",
        ["request_id"],
        unique=False,
    )


def downgrade():
    with op.batch_alter_table("request_config_file_base64") as batch_op:
        batch_op.drop_index(batch_op.f("ix_request_config_file_base64_request_id"))
        batch_op.drop_index(batch_op.f("ix_request_config_file_base64_config_file_base64_id"))

    op.drop_table("request_config_file_base64")

    with op.batch_alter_table("config_file_base64") as batch_op:
        batch_op.drop_index(batch_op.f("ix_config_file_base64_path"))
        batch_op.drop_index(batch_op.f("ix_config_file_base64_content"))

    op.drop_table("config_file_base64")
