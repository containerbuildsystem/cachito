"""Add the request_state_id foreign key.

Revision ID: 3c208b05d703
Revises: fdd6d6978386
Create Date: 2019-12-19 14:57:01.313098

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import desc as sa_desc


# revision identifiers, used by Alembic.
revision = "3c208b05d703"
down_revision = "fdd6d6978386"
branch_labels = None
depends_on = None


request_table = sa.Table(
    "request",
    sa.MetaData(),
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("request_state_id", sa.Integer(), sa.ForeignKey("request_state.id")),
)


request_state_table = sa.Table(
    "request_state",
    sa.MetaData(),
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("updated", sa.DateTime()),
    sa.Column("request_id", sa.Integer(), sa.ForeignKey("request.id")),
)


def upgrade():
    with op.batch_alter_table("request") as batch_op:
        batch_op.add_column(sa.Column("request_state_id", sa.Integer(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_request_request_state_id"), ["request_state_id"], unique=True
        )
        batch_op.create_foreign_key(
            "fk_request_state_id", "request_state", ["request_state_id"], ["id"]
        )

    connection = op.get_bind()
    for request in connection.execute(request_table.select()):
        request_id = request[0]
        last_state = connection.execute(
            request_state_table.select()
            .where(request_state_table.c.request_id == request_id)
            .order_by(sa_desc(request_state_table.c.updated))
            .limit(1)
        ).fetchone()
        if not last_state:
            continue

        last_state_id = last_state[0]
        connection.execute(
            request_table.update()
            .where(request_table.c.id == request_id)
            .values(request_state_id=last_state_id)
        )


def downgrade():
    with op.batch_alter_table("request") as batch_op:
        batch_op.drop_constraint("fk_request_state_id", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_request_request_state_id"))
        batch_op.drop_column("request_state_id")
