"""alias_cobro_alumno

Revision ID: d4e5f6a7b8c9
Revises: 9adfbcfa71fb
Create Date: 2026-05-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = '9adfbcfa71fb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'alias_cobro_alumno',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('alumno_id', sa.Integer(), nullable=False),
        sa.Column('alias', sa.String(160), nullable=False),
        sa.ForeignKeyConstraint(['alumno_id'], ['alumno.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('alumno_id', 'alias'),
    )


def downgrade() -> None:
    op.drop_table('alias_cobro_alumno')
