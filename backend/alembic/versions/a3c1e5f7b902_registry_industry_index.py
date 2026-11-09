"""corp_registry industry index (peer discovery)

Revision ID: a3c1e5f7b902
Revises: ded2a7737c6a
Create Date: 2026-11-09 10:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a3c1e5f7b902'
down_revision: Union[str, Sequence[str], None] = 'ded2a7737c6a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('corp_registry', sa.Column('induty_code', sa.String(length=10), nullable=True))
    op.add_column('corp_registry', sa.Column('corp_cls', sa.String(length=1), nullable=True))
    op.add_column('corp_registry', sa.Column('induty_checked_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_corp_registry_induty_code', 'corp_registry', ['induty_code'])


def downgrade() -> None:
    op.drop_index('ix_corp_registry_induty_code', table_name='corp_registry')
    op.drop_column('corp_registry', 'induty_checked_at')
    op.drop_column('corp_registry', 'corp_cls')
    op.drop_column('corp_registry', 'induty_code')
