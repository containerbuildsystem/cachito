"""Add RubyGems package manager

Revision ID: e16de598d00d
Revises: 92f0d370ba4d
Create Date: 2022-08-03 15:15:49.434025

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e16de598d00d"
down_revision = "92f0d370ba4d"
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
    connection.execute(package_manager_table.insert().values(name="rubygems"))


def downgrade():
    connection = op.get_bind()
    connection.execute(
        package_manager_table.delete().where(package_manager_table.c.name == "rubygems")
    )
