"""ampliar cuit varchar20

Revision ID: e1f2a3b4c5d6
Revises: d4e5f6a7b8c9
Create Date: 2026-06-01
"""
from alembic import op
import sqlalchemy as sa

revision = 'e1f2a3b4c5d6'
down_revision = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('referente_pago') as batch_op:
        batch_op.alter_column('cuit_cuil',
            existing_type=sa.String(13),
            type_=sa.String(20),
            existing_nullable=False)

    with op.batch_alter_table('movimiento_bancario') as batch_op:
        batch_op.alter_column('cuit_detectado',
            existing_type=sa.String(13),
            type_=sa.String(20),
            existing_nullable=True)


def downgrade() -> None:
    with op.batch_alter_table('movimiento_bancario') as batch_op:
        batch_op.alter_column('cuit_detectado',
            existing_type=sa.String(20),
            type_=sa.String(13),
            existing_nullable=True)

    with op.batch_alter_table('referente_pago') as batch_op:
        batch_op.alter_column('cuit_cuil',
            existing_type=sa.String(20),
            type_=sa.String(13),
            existing_nullable=False)
