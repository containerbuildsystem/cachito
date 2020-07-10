"""
Map dependencies to their associated package.

Revision ID: 1714d8e3002b
Revises: b46cf36806d7
Create Date: 2020-06-24 13:56:28.738839
"""
import logging

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "1714d8e3002b"
down_revision = "b46cf36806d7"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic")

request_dependency_table = sa.Table(
    "request_dependency",
    sa.MetaData(),
    sa.Column("request_id", sa.Integer()),
    sa.Column("dependency_id", sa.Integer()),
    sa.Column("package_id", sa.Integer()),
)

request_package_table = sa.Table(
    "request_package",
    sa.MetaData(),
    sa.Column("request_id", sa.Integer()),
    sa.Column("package_id", sa.Integer()),
)

package_table = sa.Table(
    "package",
    sa.MetaData(),
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("name", sa.String()),
    sa.Column("type", sa.String()),
    sa.Column("version", sa.String()),
)


def upgrade():
    with op.batch_alter_table("request_dependency") as batch_op:
        # Temporarily make this column nullable so it can be populated in the data migration
        batch_op.add_column(
            sa.Column("package_id", sa.Integer(), autoincrement=False, nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_request_dependency_package_id", "package", ["package_id"], ["id"]
        )
        batch_op.drop_constraint("request_dependency_request_id_dependency_id_key")

    _upgrade_data()

    with op.batch_alter_table("request_dependency") as batch_op:
        batch_op.alter_column("package_id", existing_type=sa.INTEGER(), nullable=False)
        batch_op.create_index(
            batch_op.f("ix_request_dependency_package_id"), ["package_id"], unique=False
        )
        batch_op.create_unique_constraint(
            "request_dependency_request_id_dependency_id_package_id_key",
            ["request_id", "dependency_id", "package_id"],
        )


def _update_request_deps(connection, request_id, pkg_id_to_dep_ids):
    for pkg_id, dep_ids in pkg_id_to_dep_ids.items():
        connection.execute(
            request_dependency_table.update()
            .where(request_dependency_table.c.request_id == request_id)
            .where(request_dependency_table.c.dependency_id.in_(dep_ids))
            .values(package_id=pkg_id)
        )


def _upgrade_data():
    connection = op.get_bind()
    last_request_id = None
    dummy_package_name = "cachito-migration-placeholder"
    dummy_package_version = "0.0.0"
    pkg_type_to_pkg = {}
    pkg_id_to_dep_ids = {}
    for request_dep in connection.execute(
        request_dependency_table.select().order_by(request_dependency_table.c.request_id)
    ).fetchall():
        if last_request_id != request_dep.request_id:
            if last_request_id is not None:
                # When we get to the next request, update the entries of the previous request
                _update_request_deps(connection, last_request_id, pkg_id_to_dep_ids)
            last_request_id = request_dep.request_id
            pkg_type_to_pkg = {}
            pkg_id_to_dep_ids = {}
            log.info(
                "Associating packages with the dependencies for request %d", request_dep.request_id
            )

        # Note that a package can be a top-level package or dependency
        dependency_type = connection.execute(
            sa.select([package_table.c.type]).where(package_table.c.id == request_dep.dependency_id)
        ).scalar()
        package = pkg_type_to_pkg.get(dependency_type)

        if not package:
            package = connection.execute(
                package_table.select()
                .select_from(
                    package_table.join(
                        request_package_table,
                        package_table.c.id == request_package_table.c.package_id,
                    )
                )
                .where(request_package_table.c.request_id == request_dep.request_id)
                .where(package_table.c.type == dependency_type)
            ).fetchone()
            pkg_type_to_pkg[dependency_type] = package

        if not package:
            log.warning(
                "Couldn't find a package associated with the request %d and type %s. Associating a "
                "dummy package with the request.",
                request_dep.request_id,
                dependency_type,
            )

            package = connection.execute(
                package_table.select()
                .where(package_table.c.name == dummy_package_name)
                .where(package_table.c.type == dependency_type)
                .where(package_table.c.version == dummy_package_version)
            ).fetchone()
            if not package:
                connection.execute(
                    package_table.insert().values(
                        name=dummy_package_name, type=dependency_type, version=dummy_package_version
                    )
                )
                package = connection.execute(
                    package_table.select()
                    .where(package_table.c.name == dummy_package_name)
                    .where(package_table.c.type == dependency_type)
                    .where(package_table.c.version == dummy_package_version)
                ).fetchone()

            pkg_type_to_pkg[dependency_type] = package
            connection.execute(
                request_package_table.insert().values(
                    request_id=request_dep.request_id, package_id=package.id
                )
            )

        pkg_id_to_dep_ids.setdefault(package.id, set()).add(request_dep.dependency_id)

    # Update the entries of the last request
    _update_request_deps(connection, last_request_id, pkg_id_to_dep_ids)


def downgrade():
    connection = op.get_bind()
    bad_request_id_results = connection.execute(
        sa.sql.select([request_dependency_table.c.request_id])
        .group_by(request_dependency_table.c.request_id, request_dependency_table.c.dependency_id)
        .having(sa.sql.func.count("*") > 1)
    ).fetchall()
    bad_request_ids = {req_dep.request_id for req_dep in bad_request_id_results}
    if bad_request_ids:
        raise RuntimeError(
            "The following requests have the same dependencies associated with different "
            f"packages: {', '.join(str(v) for v in bad_request_ids)}. Unable to create the proper "
            "unique constraint after the downgrade."
        )

    with op.batch_alter_table("request_dependency") as batch_op:
        batch_op.drop_constraint(
            "request_dependency_request_id_dependency_id_package_id_key", type_="unique"
        )
        batch_op.drop_constraint("fk_request_dependency_package_id", type_="foreignkey")
        batch_op.drop_index("ix_request_dependency_package_id")
        batch_op.drop_column("package_id")
        batch_op.create_unique_constraint(
            "request_dependency_request_id_dependency_id_key", ["request_id", "dependency_id"]
        )
