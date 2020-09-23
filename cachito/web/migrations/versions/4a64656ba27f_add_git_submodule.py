"""Add the git-submodule package manager

Revision ID: 4a64656ba27f
Revises: cfbbf7675e3b
Create Date: 2020-09-21 21:40:36.901272

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "4a64656ba27f"
down_revision = "cfbbf7675e3b"
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
    connection.execute(package_manager_table.insert().values(name="git-submodule"))


def downgrade():
    connection = op.get_bind()
    connection.execute(
        package_manager_table.delete().where(package_manager_table.c.name == "git-submodule")
    )
