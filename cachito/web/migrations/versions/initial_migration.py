"""An initial migration that can be deleted when there is a real migration

Revision ID: c8b2a3a26191
Create Date: 2019-04-08 17:30:01.062645

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c8b2a3a26191'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    pkg_manager_table = op.create_table(
        'package_manager',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )

    op.create_table(
        'request',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('git_repo', sa.String(), nullable=False),
        sa.Column('git_ref', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )

    op.create_table(
        'request_pkg_manager',
        sa.Column('request_id', sa.Integer(), nullable=True),
        sa.Column('pkg_manager_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['pkg_manager_id'], ['package_manager.id'], ),
        sa.ForeignKeyConstraint(['request_id'], ['request.id'], )
    )

    # Insert supported pkg managers
    op.bulk_insert(pkg_manager_table, [
        {'name': 'gomod'},
    ])


def downgrade():
    op.drop_table('request_pkg_manager')
    op.drop_table('request')
    op.drop_table('package_manager')
