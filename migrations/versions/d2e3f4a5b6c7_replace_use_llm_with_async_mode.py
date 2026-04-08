"""replace use_llm with async_mode in assignment_questions

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-04-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd2e3f4a5b6c7'
down_revision: Union[str, None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add async_mode column with default "standard"
    op.add_column(
        'assignment_questions',
        sa.Column(
            'async_mode',
            sa.String(length=20),
            nullable=True,
            server_default=sa.text("'standard'"),
        ),
    )
    # Migrate existing use_llm="T" rows to async_mode="llm"
    op.execute(
        "UPDATE assignment_questions SET async_mode = 'llm' WHERE use_llm = 'T'"
    )
    op.execute(
        "UPDATE assignment_questions SET async_mode = 'standard' WHERE async_mode IS NULL"
    )
    op.alter_column('assignment_questions', 'async_mode', nullable=False)
    # Drop the old boolean column
    op.drop_column('assignment_questions', 'use_llm')


def downgrade() -> None:
    op.add_column(
        'assignment_questions',
        sa.Column(
            'use_llm',
            sa.String(length=1),
            nullable=True,
            server_default=sa.text("'F'"),
        ),
    )
    op.execute(
        "UPDATE assignment_questions SET use_llm = 'T' WHERE async_mode = 'llm' OR async_mode = 'analogies'"
    )
    op.execute(
        "UPDATE assignment_questions SET use_llm = 'F' WHERE use_llm IS NULL"
    )
    op.alter_column('assignment_questions', 'use_llm', nullable=False)
    op.drop_column('assignment_questions', 'async_mode')
