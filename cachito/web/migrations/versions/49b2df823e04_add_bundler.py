"""Add the Bundler package manager for Ruby

Revision ID: 49b2df823e04
Revises: 97d5df7fca86
Create Date: 2021-02-11 00:20:50.070349

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '49b2df823e04'
down_revision = '97d5df7fca86'
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
    connection.execute(package_manager_table.insert().values(name="bundler"))


def downgrade():
    connection = op.get_bind()
    connection.execute(package_manager_table.delete().where(package_manager_table.c.name == "bundler"))
