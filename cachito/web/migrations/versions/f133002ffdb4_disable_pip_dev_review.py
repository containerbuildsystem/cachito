"""Disable pip-dev-preview flag"

Revision ID: f133002ffdb4
Revises: 4a64656ba27f
Create Date: 2020-09-29 06:37:57.811885

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f133002ffdb4"
down_revision = "4a64656ba27f"
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
    connection.execute(
        flag_table.update().where(flag_table.c.name == "pip-dev-preview").values(active=False)
    )


def downgrade():
    connection = op.get_bind()
    res = connection.execute(
        flag_table.select().where(flag_table.c.name == "pip-dev-preview")
    ).fetchone()
    if res is None:
        connection.execute(flag_table.insert().values(name="pip-dev-preview", active=True))
    else:
        connection.execute(
            flag_table.update().where(flag_table.c.name == "pip-dev-preview").values(active=True)
        )
