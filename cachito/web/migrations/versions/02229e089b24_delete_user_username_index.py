"""Remove User.username unique index

Revision ID: 02229e089b24
Revises: 7d979987402d
Create Date: 2021-12-15 21:37:30.582812

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "02229e089b24"
down_revision = "7d979987402d"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.drop_index("ix_user_username")


def downgrade():
    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.create_index("ix_user_username", ["username"], unique=True)
