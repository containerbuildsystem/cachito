"""Add the remove-unsafe-symlinks flag

Revision ID: 01bb0873ddcb
Revises: 02229e089b24
Create Date: 2022-03-15 15:31:11.301867

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "01bb0873ddcb"
down_revision = "02229e089b24"
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
        flag_table.select().where(flag_table.c.name == "remove-unsafe-symlinks")
    ).fetchone()
    if res is None:
        connection.execute(flag_table.insert().values(name="remove-unsafe-symlinks", active=True))
    else:
        connection.execute(
            flag_table.update()
            .where(flag_table.c.name == "remove-unsafe-symlinks")
            .values(active=True)
        )


def downgrade():
    connection = op.get_bind()
    connection.execute(flag_table.update().values(name="remove-unsafe-symlinks", active=False))
