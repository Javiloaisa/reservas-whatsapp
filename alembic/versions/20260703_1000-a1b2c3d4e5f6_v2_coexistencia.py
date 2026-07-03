"""v2 coexistencia: modo humano, clasificacion, dedup por proveedor y log_sombra

Revision ID: a1b2c3d4e5f6
Revises: 48f6c8248fb7
Create Date: 2026-07-03 10:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: str | None = '48f6c8248fb7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('clientes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('modo_humano_hasta', sa.DateTime(timezone=True), nullable=True))

    with op.batch_alter_table('mensajes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('clasificacion', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('message_id_proveedor', sa.Text(), nullable=True))
        batch_op.create_unique_constraint('uq_mensajes_message_id_proveedor', ['message_id_proveedor'])

    op.create_table('log_sombra',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('cliente_id', sa.Integer(), nullable=False),
    sa.Column('mensaje_entrante', sa.Text(), nullable=False),
    sa.Column('clasificacion', sa.Text(), nullable=False),
    sa.Column('respuesta_no_enviada', sa.Text(), nullable=True),
    sa.Column('creado_en', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['cliente_id'], ['clientes.id'], ),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('log_sombra')

    with op.batch_alter_table('mensajes', schema=None) as batch_op:
        batch_op.drop_constraint('uq_mensajes_message_id_proveedor', type_='unique')
        batch_op.drop_column('message_id_proveedor')
        batch_op.drop_column('clasificacion')

    with op.batch_alter_table('clientes', schema=None) as batch_op:
        batch_op.drop_column('modo_humano_hasta')
