"""Add the cgo-disable flag

Revision ID: 07d89c5778f2
Revises: 97d5df7fca86
Create Date: 2021-02-12 10:04:40.989666

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "07d89c5778f2"
down_revision = "97d5df7fca86"
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
        flag_table.select().where(flag_table.c.name == "cgo-disable")
    ).fetchone()
    if res is None:
        connection.execute(flag_table.insert().values(name="cgo-disable", active=True))
    else:
        connection.execute(
            flag_table.update().where(flag_table.c.name == "cgo-disable").values(active=True)
        )


def downgrade():
    connection = op.get_bind()
    connection.execute(flag_table.update().values(name="cgo-disable", active=False))
