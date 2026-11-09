"""weekly price analyses

Revision ID: c4e8a1d2f357
Revises: b7d2f4a9c613
Create Date: 2026-11-09 16:10:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c4e8a1d2f357'
down_revision: Union[str, Sequence[str], None] = 'b7d2f4a9c613'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'weekly_analyses',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('project_id', sa.String(36), sa.ForeignKey('projects.id'), nullable=False),
        sa.Column('peer_version_no', sa.Integer(), nullable=False),
        sa.Column('week_start', sa.String(10), nullable=False),
        sa.Column('week_end', sa.String(10), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('stage', sa.String(100), nullable=True),
        sa.Column('options', sa.JSON(), nullable=False),
        sa.Column('messages', sa.JSON(), nullable=False),
        sa.Column('result', sa.JSON(), nullable=False),
        sa.Column('report', sa.JSON(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_weekly_analyses_project_id', 'weekly_analyses', ['project_id'])
    op.create_index('ix_weekly_analyses_week_start', 'weekly_analyses', ['week_start'])


def downgrade() -> None:
    op.drop_table('weekly_analyses')
