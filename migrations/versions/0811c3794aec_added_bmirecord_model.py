"""Added BMIRecord model

Revision ID: 0811c3794aec
Revises: 7f05ca924e21
Create Date: 2026-03-05 10:59:44.716474

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0811c3794aec'
down_revision = '7f05ca924e21'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'bmi_records',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('height', sa.Float(), nullable=False),
        sa.Column('weight', sa.Float(), nullable=False),
        sa.Column('bmi', sa.Float(), nullable=False),
        sa.Column('category', sa.String(length=50), nullable=False),
        sa.Column('recorded_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade():
    op.drop_table('bmi_records')