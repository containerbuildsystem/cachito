"""Add table for flags

Revision ID: a655a299e967
Revises: cdf17fad3edb
Create Date: 2019-09-17 11:28:08.090670

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a655a299e967'
down_revision = 'cdf17fad3edb'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'flag',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id', 'name')
    )

    op.create_table(
        'request_flag',
        sa.Column('request_id', sa.Integer(), nullable=False),
        sa.Column('flag_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['flag_id'], ['flag.id'], ),
        sa.ForeignKeyConstraint(['request_id'], ['request.id'], ),
        sa.UniqueConstraint('request_id', 'flag_id')
    )


def downgrade():
    op.drop_table('request_flag')
    op.drop_table('flag')
