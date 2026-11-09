"""news search tables

Revision ID: b7d2f4a9c613
Revises: a3c1e5f7b902
Create Date: 2026-11-09 15:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b7d2f4a9c613'
down_revision: Union[str, Sequence[str], None] = 'a3c1e5f7b902'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'news_searches',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('query_key', sa.String(200), nullable=False),
        sa.Column('company_id', sa.Integer(), sa.ForeignKey('companies.id'), nullable=True),
        sa.Column('query', sa.Text(), nullable=False),
        sa.Column('since', sa.String(10), nullable=False),
        sa.Column('until', sa.String(10), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('item_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('model', sa.String(60), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('searched_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_news_searches_query_key', 'news_searches', ['query_key'])
    op.create_table(
        'news_items',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('query_key', sa.String(200), nullable=False),
        sa.Column('company_id', sa.Integer(), sa.ForeignKey('companies.id'), nullable=True),
        sa.Column('pub_date', sa.String(10), nullable=True),
        sa.Column('date_certain', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('outlet', sa.String(100), nullable=True),
        sa.Column('title', sa.String(400), nullable=False),
        sa.Column('url', sa.String(600), nullable=False),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('event', sa.String(200), nullable=True),
        sa.Column('sentiment', sa.String(10), nullable=True),
        sa.Column('about', sa.String(200), nullable=True),
        sa.Column('link_status', sa.String(20), nullable=False, server_default='unchecked'),
        sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('query_key', 'url', name='uq_news_query_url'),
    )
    op.create_index('ix_news_items_query_key', 'news_items', ['query_key'])
    op.create_index('ix_news_items_pub_date', 'news_items', ['pub_date'])


def downgrade() -> None:
    op.drop_table('news_items')
    op.drop_table('news_searches')
