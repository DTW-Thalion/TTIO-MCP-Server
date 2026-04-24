"""signature_columns

Revision ID: 3840d96e5185
Revises: 65fda2fc1cfe
Create Date: 2026-04-24 11:00:00.000000

Adds M7 signature-tracking columns to ``files``:
  - ``signature_algorithm`` (nullable str) — e.g. ``hmac-sha256``;
    mirrors ``encrypted_algorithm``'s role for the signing side.
  - ``signed_at`` (nullable timestamp) — when the MCP server last
    signed the file.
  - ``signed_by`` (nullable FK to ``users.id``) — who requested it.

The existing boolean ``signed`` column (added by the initial schema)
stays; it's driven by whichever of the new columns is populated.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '3840d96e5185'
down_revision: str | Sequence[str] | None = '65fda2fc1cfe'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('files', schema=None) as batch_op:
        batch_op.add_column(sa.Column('signature_algorithm', sa.String(), nullable=True))
        batch_op.add_column(
            sa.Column('signed_at', sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column('signed_by', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_files_signed_by_users',
            'users',
            ['signed_by'],
            ['id'],
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('files', schema=None) as batch_op:
        batch_op.drop_constraint('fk_files_signed_by_users', type_='foreignkey')
        batch_op.drop_column('signed_by')
        batch_op.drop_column('signed_at')
        batch_op.drop_column('signature_algorithm')
