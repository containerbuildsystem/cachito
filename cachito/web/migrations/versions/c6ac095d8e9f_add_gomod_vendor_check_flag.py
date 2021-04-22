"""Add the gomod-vendor-check flag

Revision ID: c6ac095d8e9f
Revises: f201f05a95a7
Create Date: 2021-04-15 13:45:44.761284

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c6ac095d8e9f"
down_revision = "f201f05a95a7"
branch_labels = None
depends_on = None


flag_table = sa.Table(
    "flag",
    sa.MetaData(),
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("active", sa.Boolean(), nullable=False, default=True),
)


def upgrade():
    connection = op.get_bind()
    res = connection.execute(
        flag_table.select().where(flag_table.c.name == "gomod-vendor-check")
    ).fetchone()
    if res is None:
        connection.execute(flag_table.insert().values(name="gomod-vendor-check", active=True))
    else:
        connection.execute(
            flag_table.update().where(flag_table.c.name == "gomod-vendor-check").values(active=True)
        )


def downgrade():
    connection = op.get_bind()
    connection.execute(flag_table.update().values(name="gomod-vendor-check", active=False))
