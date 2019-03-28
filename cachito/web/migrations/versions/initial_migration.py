"""An initial migration that can be deleted when there is a real migration

Revision ID: 20ed360ce0c8
Create Date: 2019-03-28 12:48:46.214233

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20ed360ce0c8'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'request',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade():
    op.drop_table('request')
