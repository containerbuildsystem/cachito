"""Add the pip package manager

Revision ID: cfbbf7675e3b
Revises: 044548f3f83a
Create Date: 2020-09-04 18:38:07.060924

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "cfbbf7675e3b"
down_revision = "044548f3f83a"
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
    connection.execute(package_manager_table.insert().values(name="pip"))


def downgrade():
    connection = op.get_bind()
    connection.execute(package_manager_table.delete().where(package_manager_table.c.name == "pip"))
