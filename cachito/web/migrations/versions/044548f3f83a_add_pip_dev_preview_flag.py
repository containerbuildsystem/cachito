"""Add the pip-dev-preview flag

Revision ID: 044548f3f83a
Revises: cb6bdbe533cc
Create Date: 2020-09-03 02:16:57.554550

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "044548f3f83a"
down_revision = "cb6bdbe533cc"
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
        flag_table.select().where(flag_table.c.name == "pip-dev-preview")
    ).fetchone()
    if res is None:
        connection.execute(flag_table.insert().values(name="pip-dev-preview", active=True))
    else:
        connection.execute(
            flag_table.update().where(flag_table.c.name == "pip-dev-preview").values(active=True)
        )


def downgrade():
    connection = op.get_bind()
    connection.execute(flag_table.update().values(name="pip-dev-preview", active=False))
