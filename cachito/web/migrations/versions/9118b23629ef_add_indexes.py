"""Add indexes to commonly used columns

Revision ID: 9118b23629ef
Revises: 5854e700a35e
Create Date: 2019-10-15 16:56:37.362817

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = '9118b23629ef'
down_revision = '5854e700a35e'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(op.f('ix_dependency_name'), 'dependency', ['name'], unique=False)
    op.create_index(op.f('ix_dependency_type'), 'dependency', ['type'], unique=False)
    op.create_index(op.f('ix_dependency_version'), 'dependency', ['version'], unique=False)
    op.create_index(
        op.f('ix_request_environment_variable_env_var_id'),
        'request_environment_variable',
        ['env_var_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_request_environment_variable_request_id'),
        'request_environment_variable',
        ['request_id'],
        unique=False,
    )
    op.create_index(op.f('ix_request_flag_flag_id'), 'request_flag', ['flag_id'], unique=False)
    op.create_index(
        op.f('ix_request_flag_request_id'), 'request_flag', ['request_id'], unique=False
    )
    op.create_index(
        op.f('ix_request_pkg_manager_pkg_manager_id'),
        'request_pkg_manager',
        ['pkg_manager_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_request_pkg_manager_request_id'),
        'request_pkg_manager',
        ['request_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_request_state_request_id'), 'request_state', ['request_id'], unique=False
    )
    op.create_index(op.f('ix_user_username'), 'user', ['username'], unique=True)


def downgrade():
    op.drop_index(op.f('ix_user_username'), table_name='user')
    op.drop_index(op.f('ix_request_state_request_id'), table_name='request_state')
    op.drop_index(op.f('ix_request_pkg_manager_request_id'), table_name='request_pkg_manager')
    op.drop_index(op.f('ix_request_pkg_manager_pkg_manager_id'), table_name='request_pkg_manager')
    op.drop_index(op.f('ix_request_flag_request_id'), table_name='request_flag')
    op.drop_index(op.f('ix_request_flag_flag_id'), table_name='request_flag')
    op.drop_index(
        op.f('ix_request_environment_variable_request_id'),
        table_name='request_environment_variable',
    )
    op.drop_index(
        op.f('ix_request_environment_variable_env_var_id'),
        table_name='request_environment_variable',
    )
    op.drop_index(op.f('ix_dependency_version'), table_name='dependency')
    op.drop_index(op.f('ix_dependency_type'), table_name='dependency')
    op.drop_index(op.f('ix_dependency_name'), table_name='dependency')
