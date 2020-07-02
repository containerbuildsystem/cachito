"""Add environment variables types

Revision ID: cb6bdbe533cc
Revises: 615c19a1cee1
Create Date: 2020-06-22 14:56:53.584293

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "cb6bdbe533cc"
down_revision = "1714d8e3002b"
branch_labels = None
depends_on = None


env_var_table = sa.Table(
    "environment_variable",
    sa.MetaData(),
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("value", sa.String, nullable=False),
    sa.Column("kind", sa.String, nullable=False),
)


def upgrade():
    with op.batch_alter_table("environment_variable") as batch_op:
        batch_op.drop_constraint("environment_variable_name_value_key", type_="unique")
        # Make this nullable initially, so we can adjust the data first
        batch_op.add_column(sa.Column("kind", sa.String(), nullable=True))

    connection = op.get_bind()
    connection.execute(
        env_var_table.update().where(env_var_table.c.kind == sa.null()).values(kind="path")
    )

    with op.batch_alter_table("environment_variable") as batch_op:
        batch_op.create_unique_constraint(
            "environment_variable_name_value_kind_key", ["name", "value", "kind"]
        )
        batch_op.alter_column("kind", nullable=False)


def downgrade():
    with op.batch_alter_table("environment_variable") as batch_op:
        batch_op.drop_constraint("environment_variable_name_value_kind_key", type_="unique")
        batch_op.drop_column("kind")
        batch_op.create_unique_constraint("environment_variable_name_value_key", ["name", "value"])
