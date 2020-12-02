"""Add the include-git-dir flag

Revision ID: 97d5df7fca86
Revises: eff9db96576e
Create Date: 2020-12-02 12:24:05.149962
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "97d5df7fca86"
down_revision = "eff9db96576e"
branch_labels = None
depends_on = None

flag_table = sa.Table(
    "flag",
    sa.MetaData(),
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("active", sa.Boolean(), nullable=False, default=True),
)

flag_name = "include-git-dir"


def upgrade():
    connection = op.get_bind()
    res = connection.execute(flag_table.select().where(flag_table.c.name == flag_name)).fetchone()
    if res is None:
        connection.execute(flag_table.insert().values(name=flag_name, active=True))
    else:
        connection.execute(
            flag_table.update().where(flag_table.c.name == flag_name).values(active=True)
        )


def downgrade():
    connection = op.get_bind()
    connection.execute(flag_table.update().values(name=flag_name, active=False))
