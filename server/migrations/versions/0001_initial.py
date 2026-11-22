"""Initial accounts, profiles, records, and buddies.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("userid", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "profiles",
        sa.Column("profileid", sa.BigInteger(), primary_key=True),
        sa.Column("userid", sa.BigInteger(), sa.ForeignKey("accounts.userid"), nullable=False),
        sa.Column("gamecd", sa.Text(), nullable=False),
        sa.Column("uniquenick", sa.Text(), nullable=False, server_default=""),
        sa.Column("lastname", sa.Text(), nullable=False, server_default=""),
        sa.Column("firstname", sa.Text(), nullable=False, server_default=""),
        sa.Column("nick", sa.Text(), nullable=False, server_default=""),
        sa.Column("email", sa.Text(), nullable=False, server_default=""),
        sa.Column("partnerid", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("userid", "gamecd", name="uq_profiles_userid_gamecd"),
    )
    op.create_table(
        "records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("gamecd", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "owner_profileid",
            sa.BigInteger(),
            sa.ForeignKey("profiles.profileid", onupdate="CASCADE"),
            nullable=False,
        ),
        sa.Column("slot", sa.Text(), nullable=False, server_default=""),
        sa.Column("label", sa.Text(), nullable=False, server_default=""),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("gamecd", "kind", "owner_profileid", "slot", name="uq_records_slot"),
    )
    op.create_table(
        "buddies",
        sa.Column("owner_profileid", sa.BigInteger(), primary_key=True),
        sa.Column("friend_profileid", sa.BigInteger(), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("buddies")
    op.drop_table("records")
    op.drop_table("profiles")
    op.drop_table("accounts")
