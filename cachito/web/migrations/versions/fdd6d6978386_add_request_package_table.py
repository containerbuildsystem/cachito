"""Add the request_package table

Revision ID: fdd6d6978386
Revises: 15c4aa0c4144
Create Date: 2019-11-12 11:54:18.760937

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'fdd6d6978386'
down_revision = '15c4aa0c4144'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'request_package',
        sa.Column('request_id', sa.Integer(), autoincrement=False, nullable=False),
        sa.Column('package_id', sa.Integer(), autoincrement=False, nullable=False),
        sa.ForeignKeyConstraint(['package_id'], ['package.id']),
        sa.ForeignKeyConstraint(['request_id'], ['request.id']),
        sa.PrimaryKeyConstraint('request_id', 'package_id'),
        sa.UniqueConstraint('request_id', 'package_id'),
    )
    with op.batch_alter_table('request_package', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_request_package_package_id'), ['package_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_request_package_request_id'), ['request_id'], unique=False
        )


def downgrade():
    with op.batch_alter_table('request_package', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_request_package_request_id'))
        batch_op.drop_index(batch_op.f('ix_request_package_package_id'))

    op.drop_table('request_package')
