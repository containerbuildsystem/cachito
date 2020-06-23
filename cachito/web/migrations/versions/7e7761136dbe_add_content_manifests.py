"""Add support for content manifests

Revision ID: 7e7761136dbe
Revises: 615c19a1cee1
Create Date: 2020-06-22 16:56:43.702045

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "7e7761136dbe"
down_revision = "615c19a1cee1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "content_manifest",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("json_data", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("content_manifest", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_content_manifest_json_data"), ["json_data"], unique=False
        )

    with op.batch_alter_table("request", schema=None) as batch_op:
        batch_op.add_column(sa.Column("content_manifest_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "content_manifest_id_fkey", "content_manifest", ["content_manifest_id"], ["id"]
        )


def downgrade():
    with op.batch_alter_table("request", schema=None) as batch_op:
        batch_op.drop_constraint("content_manifest_id_fkey", type_="foreignkey")
        batch_op.drop_column("content_manifest_id")

    with op.batch_alter_table("content_manifest", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_content_manifest_json_data"))

    op.drop_table("content_manifest")
