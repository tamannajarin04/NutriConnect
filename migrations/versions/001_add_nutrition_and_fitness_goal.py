"""Add nutrition fields to meal_logs and create fitness_goals table

Revision ID: 001_nutrition_fitness
Revises: 007801f33906
Create Date: 2025-01-01 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '001_nutrition_fitness'
down_revision = 'e2468a06d8ba'
branch_labels = None
depends_on = None


def upgrade():
    # ── Add nutrition columns to meal_logs ───────────────────────────
    with op.batch_alter_table('meal_logs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('calories',             sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('protein',              sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('carbs',                sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('fat',                  sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('nutrition_source',     sa.String(30), nullable=True))
        batch_op.add_column(sa.Column('nutrition_confidence', sa.String(10), nullable=True))
        batch_op.add_column(sa.Column('is_ai_estimated',      sa.Boolean(), nullable=False, server_default='false'))
        batch_op.add_column(sa.Column('parsed_items_json',    sa.Text(), nullable=True))

    # ── Create fitness_goals table ────────────────────────────────────
    op.create_table(
        'fitness_goals',
        sa.Column('id',             sa.Integer(),  nullable=False),
        sa.Column('user_id',        sa.Integer(),  nullable=False),
        sa.Column('current_weight', sa.Float(),    nullable=False),
        sa.Column('target_weight',  sa.Float(),    nullable=False),
        sa.Column('height_cm',      sa.Float(),    nullable=False),
        sa.Column('age',            sa.Integer(),  nullable=False),
        sa.Column('gender',         sa.String(10), nullable=False),
        sa.Column('activity_level', sa.Float(),    nullable=False),
        sa.Column('daily_calories', sa.Integer(),  nullable=False),
        sa.Column('tdee',           sa.Integer(),  nullable=True),
        sa.Column('daily_deficit',  sa.Integer(),  nullable=True),
        sa.Column('ai_result_json', sa.Text(),     nullable=True),
        sa.Column('created_at',     sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_fitness_goals_user_id', 'fitness_goals', ['user_id'])


def downgrade():
    op.drop_index('ix_fitness_goals_user_id', table_name='fitness_goals')
    op.drop_table('fitness_goals')

    with op.batch_alter_table('meal_logs', schema=None) as batch_op:
        batch_op.drop_column('parsed_items_json')
        batch_op.drop_column('is_ai_estimated')
        batch_op.drop_column('nutrition_confidence')
        batch_op.drop_column('nutrition_source')
        batch_op.drop_column('fat')
        batch_op.drop_column('carbs')
        batch_op.drop_column('protein')
        batch_op.drop_column('calories')