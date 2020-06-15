"""Add the gomod-vendor flag

Revision ID: b46cf36806d7
Revises: 615c19a1cee1
Create Date: 2020-05-19 10:33:19.638354
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b46cf36806d7"
down_revision = "615c19a1cee1"
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
        flag_table.select().where(flag_table.c.name == "gomod-vendor")
    ).fetchone()
    if res is None:
        connection.execute(flag_table.insert().values(name="gomod-vendor", active=True))
    else:
        connection.execute(
            flag_table.update().where(flag_table.c.name == "gomod-vendor").values(active=True)
        )


def downgrade():
    connection = op.get_bind()
    connection.execute(flag_table.update().values(name="gomod-vendor", active=False))
