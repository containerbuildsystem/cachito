"""Adjust unique constraint on Flag table

Revision ID: 4d17dec0cfc3
Revises: c6ac095d8e9f
Create Date: 2021-05-25 13:21:48.298960

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "4d17dec0cfc3"
down_revision = "c6ac095d8e9f"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("flag") as batch_op:
        batch_op.create_unique_constraint("flag_name_key", ["name"])
        batch_op.drop_constraint("flag_id_name_key", type_="unique")


def downgrade():
    with op.batch_alter_table("flag") as batch_op:
        batch_op.drop_constraint("flag_name_key", type_="unique")
        batch_op.create_unique_constraint("flag_id_name_key", ["id", "name"])
