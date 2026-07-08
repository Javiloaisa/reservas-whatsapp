"""mensajes.traza_tools: persistir rondas de tool use del agente

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-08 20:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: str | None = 'a1b2c3d4e5f6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('mensajes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('traza_tools', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('mensajes', schema=None) as batch_op:
        batch_op.drop_column('traza_tools')
