"""Add the npm package manager

Revision ID: 615c19a1cee1
Revises: 193baf9d7cbf
Create Date: 2020-04-06 19:50:06.577126

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "615c19a1cee1"
down_revision = "193baf9d7cbf"
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
    connection.execute(package_manager_table.insert().values(name="npm"))

    with op.batch_alter_table("package") as batch_op:
        batch_op.add_column(
            sa.Column("dev", sa.Boolean(), server_default=sa.text("false"), nullable=False)
        )
        batch_op.create_index(batch_op.f("ix_package_dev"), ["dev"], unique=False)
        batch_op.drop_constraint("dependency_name_type_version_key", type_="unique")
        batch_op.create_unique_constraint(
            "dependency_dev_name_type_version_key", ["dev", "name", "type", "version"]
        )


def downgrade():
    connection = op.get_bind()
    connection.execute(package_manager_table.delete().where(package_manager_table.c.name == "npm"))

    with op.batch_alter_table("package") as batch_op:
        batch_op.drop_constraint("dependency_dev_name_type_version_key", type_="unique")
        batch_op.drop_column("dev")
        batch_op.create_unique_constraint(
            "dependency_name_type_version_key", ["name", "type", "version"]
        )
        batch_op.drop_index(batch_op.f("ix_package_dev"))
