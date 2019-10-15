"""Add the replaced_dependency_id column and indexes on the request_dependency table

Revision ID: 5854e700a35e
Revises: a655a299e967
Create Date: 2019-10-14 16:33:01.601651

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5854e700a35e'
down_revision = 'a655a299e967'
branch_labels = None
depends_on = None


def upgrade():
    # Must use batch_alter_table to support SQLite
    with op.batch_alter_table('request_dependency') as b:
        b.add_column(sa.Column('replaced_dependency_id', sa.Integer(), nullable=True))
        b.create_foreign_key(
            'fk_replaced_dependency_id', 'dependency', ['replaced_dependency_id'], ['id']
        )

    op.create_index(
        op.f('ix_request_dependency_dependency_id'),
        'request_dependency',
        ['dependency_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_request_dependency_replaced_dependency_id'),
        'request_dependency',
        ['replaced_dependency_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_request_dependency_request_id'), 'request_dependency', ['request_id'], unique=False
    )


def downgrade():
    op.drop_index(op.f('ix_request_dependency_request_id'), table_name='request_dependency')
    op.drop_index(
        op.f('ix_request_dependency_replaced_dependency_id'), table_name='request_dependency'
    )
    op.drop_index(op.f('ix_request_dependency_dependency_id'), table_name='request_dependency')
    # Must use batch_alter_table to support SQLite
    with op.batch_alter_table('request_dependency') as b:
        b.drop_constraint('fk_replaced_dependency_id', type_='foreignkey')
        b.drop_column('replaced_dependency_id')
