"""Add the submitted_by_id column

Revision ID: 71909d479045
Revises: 9118b23629ef
Create Date: 2019-10-21 13:38:48.372486

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "71909d479045"
down_revision = "9118b23629ef"
branch_labels = None
depends_on = None


def upgrade():
    # Must use batch_alter_table to support SQLite
    with op.batch_alter_table("request") as b:
        b.add_column(sa.Column("submitted_by_id", sa.Integer(), nullable=True))
        b.create_foreign_key("fk_submitted_by_id", "user", ["submitted_by_id"], ["id"])


def downgrade():
    # Must use batch_alter_table to support SQLite
    with op.batch_alter_table("request") as b:
        b.drop_constraint("fk_submitted_by_id", type_="foreignkey")
        b.drop_column("submitted_by_id")
