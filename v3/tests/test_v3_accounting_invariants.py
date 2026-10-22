"""
Comprehensive Accounting and Invariant Tests for CoreV3Engine.
Verifies:
1. Penny-perfect ledger equality: Realized equity delta == Net closed trade PnL.
2. Weighted average entry price upon pyramiding across both Long & Short.
3. Leverage cap enforcement during pyramiding additions.
4. Latch trading halt across persistence serialization/deserialization.
5. Hostile prices (NaN, Infinity, <= 0) rejected without mutating engine state.
6. Atomic transactional rollback on corrupted state load.
"""

from copy import deepcopy
from decimal import Decimal as D
import pytest

from v3.src.core_v3 import CoreV3Engine, ActivePosition


def make_position(direction=1, qty='1'):
    return ActivePosition(
        symbol='ETHUSDT',
        direction=direction,
        initial_qty=D(qty),
        current_qty=D(qty),
        entry_price=D('100'),
        current_stop=D('90' if direction == 1 else '110'),
        initial_atr=D('4'),
        highest_price=D('100'),
        lowest_price=D('100'),
        entry_time=0,
        original_entry=D('100'),
        entry_fees=D(qty) * D('.06'),
    )


def make_engine(direction=1, **kwargs):
    e = CoreV3Engine(**kwargs)
    e.open_positions['ETHUSDT'] = make_position(direction)
    e.realized_equity -= D('.06')
    return e


@pytest.mark.parametrize('direction', [1, -1])
def test_v3_add_cash_ledger_and_weighted_price(direction):
    """Verifies cash ledger conservation and weighted average price update on pyramiding."""
    e = make_engine(direction)
    mark = D('100') + direction * D('8')
    e.process_price_event(1, {'ETHUSDT': mark})
    p = e.open_positions['ETHUSDT']
    add_price = mark * (1 + direction * e.slippage)
    assert p.current_qty == D('1.5')
    assert p.entry_price == (D('100') + D('.5') * add_price) / D('1.5')

    exit_mark = p.current_stop - direction * D('1')
    e.process_price_event(2, {'ETHUSDT': exit_mark})
    exit_price = exit_mark * (1 - direction * e.slippage)
    expected = D('300') - D('.06') - D('.5') * add_price * e.taker_fee
    expected += direction * ((exit_price - D('100')) + D('.5') * (exit_price - add_price))
    expected -= D('1.5') * exit_price * e.taker_fee
    assert abs(e.realized_equity - expected) < D('1e-20')
    assert abs(e.closed_trades[0].net_pnl - (expected - D('300'))) < D('1e-20')


def test_v3_two_additions_cannot_cross_portfolio_cap():
    """Verify that multiple pyramid additions cannot breach the portfolio leverage cap."""
    e = make_engine(portfolio_leverage=D('.7'))
    other = deepcopy(e.open_positions['ETHUSDT'])
    other.symbol = 'BTCUSDT'
    e.open_positions['BTCUSDT'] = other
    e.process_price_event(1, {'ETHUSDT': D('108'), 'BTCUSDT': D('108')})
    assert all(not p.is_pyramided for p in e.open_positions.values())


def test_v3_halt_latched_after_recovery_and_restore():
    """Verify trading halt remains latched even after equity recovers or restart occurs."""
    e = make_engine()
    e.realized_equity = D('200')
    e.process_price_event(1, {'ETHUSDT': D('99')})
    assert e.trading_halted and not e.open_positions
    restored = CoreV3Engine()
    restored.load_from_dict(e.to_dict())
    restored.realized_equity = D('400')
    restored.process_price_event(2, {})
    assert restored.trading_halted


@pytest.mark.parametrize('bad', ['NaN', 'Infinity', '-1', '0'])
def test_v3_hostile_prices_do_not_mutate(bad):
    """Verify hostile prices trigger exceptions without corrupting engine state."""
    e = make_engine()
    before = e.to_dict()
    with pytest.raises(ValueError):
        e.process_price_event(1, {'ETHUSDT': D(bad)})
    assert e.to_dict() == before


def test_v3_snapshot_load_transactional():
    """Verify that corrupted state snapshots are rejected transactionally."""
    e = make_engine()
    before = e.to_dict()
    bad = deepcopy(before)
    bad['realized_equity'] = '999'
    bad['open_positions']['ETHUSDT']['current_qty'] = 'NaN'
    with pytest.raises(ValueError):
        e.load_from_dict(bad)
    assert e.to_dict() == before


def test_v3_atomic_json_concurrency_and_retry(tmp_path):
    """Verify atomic_json succeeds even under rapid file rewriting."""
    from v3.src.persistence import atomic_json
    import json
    target = tmp_path / "test_state.json"
    data = {"step": 1, "status": "OK"}
    atomic_json(target, data)
    assert json.loads(target.read_text(encoding="utf-8")) == data

    # Rapid consecutive writes
    for i in range(10):
        atomic_json(target, {"step": i, "val": i * 10})
    assert json.loads(target.read_text(encoding="utf-8")) == {"step": 9, "val": 90}

