"""Add the yarn package manager

Revision ID: eff9db96576e
Revises: 2f83b3e4c5cc
Create Date: 2020-11-10 23:18:10.607458

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "eff9db96576e"
down_revision = "2f83b3e4c5cc"
branch_labels = None
depends_on = None


package_manager_table = sa.Table(
    "package_manager",
    sa.MetaData(),
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
)


def upgrade():
    connection = op.get_bind()
    connection.execute(package_manager_table.insert().values(name="yarn"))


def downgrade():
    connection = op.get_bind()
    connection.execute(package_manager_table.delete().where(package_manager_table.c.name == "yarn"))
