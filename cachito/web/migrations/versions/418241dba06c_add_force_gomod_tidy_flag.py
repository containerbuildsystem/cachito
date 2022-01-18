"""Add the force-gomod-tidy flag

Revision ID: 418241dba06c
Revises: 01bb0873ddcb
Create Date: 2022-01-18 07:24:31.927867

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "418241dba06c"
down_revision = "01bb0873ddcb"
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
        flag_table.select().where(flag_table.c.name == "force-gomod-tidy")
    ).fetchone()
    if res is None:
        connection.execute(flag_table.insert().values(name="force-gomod-tidy", active=True))
    else:
        connection.execute(
            flag_table.update().where(flag_table.c.name == "force-gomod-tidy").values(active=True)
        )


def downgrade():
    connection = op.get_bind()
    connection.execute(
        flag_table.update().where(flag_table.c.name == "force-gomod-tidy").values(active=False)
    )
