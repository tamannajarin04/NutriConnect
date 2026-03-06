"""add role upgrade requests

Revision ID: b950fbb68436
Revises: 7f05ca924e21
Create Date: 2026-03-06 02:15:58.725796

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b950fbb68436'
down_revision = '7f05ca924e21'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('role_upgrade_requests', sa.Column('note', sa.Text(), nullable=True))

    with op.batch_alter_table('role_upgrade_requests', schema=None) as batch_op:
        batch_op.alter_column('status', existing_type=sa.String(length=20), nullable=False)
        batch_op.alter_column('created_at', existing_type=sa.DateTime(), nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(), nullable=False)


def downgrade():
    with op.batch_alter_table('role_upgrade_requests', schema=None) as batch_op:
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(), nullable=True)
        batch_op.alter_column('created_at', existing_type=sa.DateTime(), nullable=True)
        batch_op.alter_column('status', existing_type=sa.String(length=20), nullable=True)

    op.drop_column('role_upgrade_requests', 'note')