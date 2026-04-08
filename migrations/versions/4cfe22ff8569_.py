"""create food_nutrition_cache only

Revision ID: 4cfe22ff8569
Revises:
Create Date: 2026-04-04 05:11:03.248948
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "4cfe22ff8569"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "food_nutrition_cache",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("normalized_name", sa.String(length=120), nullable=False),
        sa.Column("calories", sa.Float(), nullable=True),
        sa.Column("protein", sa.Float(), nullable=True),
        sa.Column("carbs", sa.Float(), nullable=True),
        sa.Column("fat", sa.Float(), nullable=True),
        sa.Column("nutrition_source", sa.String(length=50), nullable=False, server_default="ai_estimate"),
        sa.Column("nutrition_confidence", sa.String(length=20), nullable=False, server_default="medium"),
        sa.Column("nutrition_basis", sa.String(length=20), nullable=False, server_default="100g"),
        sa.Column("is_ai_estimated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_nutrition_sync", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_food_nutrition_cache_normalized_name",
        "food_nutrition_cache",
        ["normalized_name"],
        unique=True,
    )


def downgrade():
    op.drop_index("ix_food_nutrition_cache_normalized_name", table_name="food_nutrition_cache")
    op.drop_table("food_nutrition_cache")