"""merge migrations

Revision ID: 41cff12e1da9
Revises: 0811c3794aec, b950fbb68436
Create Date: 2026-03-06 21:57:50.573616

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '41cff12e1da9'
down_revision = ('0811c3794aec', 'b950fbb68436')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
