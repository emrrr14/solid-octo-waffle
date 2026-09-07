"""Initial schema: users, portfolios, instruments, market data, decisions.

Generated from app.db.models and reviewed by hand - autogenerate is a drafting
tool, not an authority. Everything here is additive, so downgrade is a clean
drop; later migrations that touch data need a downgrade that is honest about
what it cannot restore.

Revision ID: 0001_initial
Revises: 
Create Date: 2026-09-07 05:42:39.446265+00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('daily_close',
    sa.Column('symbol', sa.String(length=32), nullable=False),
    sa.Column('close_date', sa.Date(), nullable=False),
    sa.Column('close', sa.Numeric(precision=18, scale=6), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.PrimaryKeyConstraint('symbol', 'close_date')
    )
    op.create_table('fund_nav',
    sa.Column('fund_code', sa.String(length=16), nullable=False),
    sa.Column('nav_date', sa.Date(), nullable=False),
    sa.Column('nav', sa.Numeric(precision=18, scale=6), nullable=False),
    sa.Column('fund_type', sa.String(length=3), nullable=False),
    sa.Column('shares', sa.Numeric(precision=24, scale=4), nullable=True),
    sa.Column('investors', sa.Integer(), nullable=True),
    sa.PrimaryKeyConstraint('fund_code', 'nav_date')
    )
    op.create_table('instruments',
    sa.Column('symbol', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('venue', sa.String(length=16), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('asset_class', sa.String(length=32), nullable=False),
    sa.Column('streaming', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('symbol')
    )
    op.create_table('macro_events',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('series', sa.String(length=32), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('observed_on', sa.Date(), nullable=False),
    sa.Column('previous_value', sa.Float(), nullable=False),
    sa.Column('new_value', sa.Float(), nullable=False),
    sa.Column('change_bps', sa.Float(), nullable=False),
    sa.Column('surprise_bps', sa.Float(), nullable=False),
    sa.Column('triggered_rebalance', sa.Boolean(), nullable=False),
    sa.Column('note', sa.String(length=256), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('series', 'observed_on', 'new_value', name='uq_macro_event')
    )
    with op.batch_alter_table('macro_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_macro_events_observed_on'), ['observed_on'], unique=False)
        batch_op.create_index(batch_op.f('ix_macro_events_series'), ['series'], unique=False)

    op.create_table('users',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('password_hash', sa.String(length=256), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_email'), ['email'], unique=True)

    op.create_table('portfolios',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('base_currency', sa.String(length=3), nullable=False),
    sa.Column('cash', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('notional', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('mandate', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('portfolios', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_portfolios_user_id'), ['user_id'], unique=False)

    op.create_table('refresh_tokens',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('family_id', sa.String(length=32), nullable=False),
    sa.Column('issued_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked', sa.Boolean(), nullable=False),
    sa.Column('device', sa.String(length=128), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('refresh_tokens', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_refresh_tokens_family_id'), ['family_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_refresh_tokens_token_hash'), ['token_hash'], unique=True)
        batch_op.create_index(batch_op.f('ix_refresh_tokens_user_id'), ['user_id'], unique=False)

    op.create_table('positions',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('portfolio_id', sa.String(length=64), nullable=False),
    sa.Column('symbol', sa.String(length=32), nullable=False),
    sa.Column('quantity', sa.Numeric(precision=24, scale=8), nullable=False),
    sa.Column('avg_cost', sa.Numeric(precision=18, scale=6), nullable=False),
    sa.ForeignKeyConstraint(['portfolio_id'], ['portfolios.id'], ),
    sa.ForeignKeyConstraint(['symbol'], ['instruments.symbol'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('portfolio_id', 'symbol', name='uq_position')
    )
    with op.batch_alter_table('positions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_positions_portfolio_id'), ['portfolio_id'], unique=False)

    op.create_table('rebalance_decisions',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('portfolio_id', sa.String(length=64), nullable=False),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('trigger_reason', sa.String(length=256), nullable=False),
    sa.Column('scenario', sa.JSON(), nullable=False),
    sa.Column('model_version', sa.String(length=64), nullable=False),
    sa.Column('weights_before', sa.JSON(), nullable=False),
    sa.Column('weights_after', sa.JSON(), nullable=False),
    sa.Column('amounts', sa.JSON(), nullable=False),
    sa.Column('orders', sa.JSON(), nullable=False),
    sa.Column('expected_return', sa.Float(), nullable=False),
    sa.Column('risk_mad', sa.Float(), nullable=False),
    sa.Column('turnover', sa.Float(), nullable=False),
    sa.Column('binding_constraints', sa.JSON(), nullable=False),
    sa.Column('solver_status', sa.String(length=128), nullable=False),
    sa.Column('applied', sa.Boolean(), nullable=False),
    sa.Column('note', sa.String(length=256), nullable=False),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['portfolio_id'], ['portfolios.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('rebalance_decisions', schema=None) as batch_op:
        batch_op.create_index('ix_decisions_portfolio_time', ['portfolio_id', 'decided_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_rebalance_decisions_portfolio_id'), ['portfolio_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('rebalance_decisions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_rebalance_decisions_portfolio_id'))
        batch_op.drop_index('ix_decisions_portfolio_time')

    op.drop_table('rebalance_decisions')
    with op.batch_alter_table('positions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_positions_portfolio_id'))

    op.drop_table('positions')
    with op.batch_alter_table('refresh_tokens', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_refresh_tokens_user_id'))
        batch_op.drop_index(batch_op.f('ix_refresh_tokens_token_hash'))
        batch_op.drop_index(batch_op.f('ix_refresh_tokens_family_id'))

    op.drop_table('refresh_tokens')
    with op.batch_alter_table('portfolios', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_portfolios_user_id'))

    op.drop_table('portfolios')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_email'))

    op.drop_table('users')
    with op.batch_alter_table('macro_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_macro_events_series'))
        batch_op.drop_index(batch_op.f('ix_macro_events_observed_on'))

    op.drop_table('macro_events')
    op.drop_table('instruments')
    op.drop_table('fund_nav')
    op.drop_table('daily_close')
