"""Rename the dependency table to package

Revision ID: 15c4aa0c4144
Revises: 71909d479045
Create Date: 2019-11-11 16:51:50.917910

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "15c4aa0c4144"
down_revision = "71909d479045"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("dependency", schema=None) as batch_op:
        batch_op.drop_index("ix_dependency_name")
        batch_op.drop_index("ix_dependency_type")
        batch_op.drop_index("ix_dependency_version")

    with op.batch_alter_table("request_dependency", schema=None) as batch_op:
        batch_op.drop_constraint("request_dependency_request_id_fkey", type_="foreignkey")
        batch_op.drop_constraint("request_dependency_dependency_id_fkey", type_="foreignkey")
        batch_op.drop_constraint("fk_replaced_dependency_id", type_="foreignkey")

    op.rename_table("dependency", "package")

    with op.batch_alter_table("request_dependency", schema=None) as batch_op:
        batch_op.create_foreign_key(
            "request_dependency_request_id_fkey", "request", ["request_id"], ["id"]
        )
        batch_op.create_foreign_key(
            "request_dependency_dependency_id_fkey", "package", ["dependency_id"], ["id"]
        )
        batch_op.create_foreign_key(
            "fk_replaced_dependency_id", "package", ["replaced_dependency_id"], ["id"]
        )

    with op.batch_alter_table("package", schema=None) as batch_op:
        batch_op.create_index(op.f("ix_package_name"), ["name"], unique=False)
        batch_op.create_index(op.f("ix_package_type"), ["type"], unique=False)
        batch_op.create_index(op.f("ix_package_version"), ["version"], unique=False)


def downgrade():
    with op.batch_alter_table("package", schema=None) as batch_op:
        batch_op.drop_index(op.f("ix_package_version"))
        batch_op.drop_index(op.f("ix_package_type"))
        batch_op.drop_index(op.f("ix_package_name"))

    with op.batch_alter_table("request_dependency", schema=None) as batch_op:
        batch_op.drop_constraint("request_dependency_request_id_fkey", type_="foreignkey")
        batch_op.drop_constraint("request_dependency_dependency_id_fkey", type_="foreignkey")
        batch_op.drop_constraint("fk_replaced_dependency_id", type_="foreignkey")

    op.rename_table("package", "dependency")

    with op.batch_alter_table("request_dependency", schema=None) as batch_op:
        batch_op.create_foreign_key(
            "request_dependency_request_id_fkey", "request", ["request_id"], ["id"]
        )
        batch_op.create_foreign_key(
            "request_dependency_dependency_id_fkey", "dependency", ["dependency_id"], ["id"]
        )
        batch_op.create_foreign_key(
            "fk_replaced_dependency_id", "dependency", ["replaced_dependency_id"], ["id"]
        )

    with op.batch_alter_table("dependency", schema=None) as batch_op:
        batch_op.create_index("ix_dependency_version", ["version"], unique=False)
        batch_op.create_index("ix_dependency_type", ["type"], unique=False)
        batch_op.create_index("ix_dependency_name", ["name"], unique=False)
