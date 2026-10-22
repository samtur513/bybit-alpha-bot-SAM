import json
import queue
import random
from copy import deepcopy
from decimal import Decimal as D
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from v2_1.src.core_v2 import CoreV2Engine
from v2_1.src.kelly_sizing import KellyRiskManager
from v2_1.src.market_stream import MarketStream
from v2_1.src.multi_scanner import MultiCoinScanner
from v2_1.src.persistence import atomic_json
from v2_1.src.pyramiding_engine import ActivePosition
from v2_1.src.research_v2_runner import prepare_symbol_indicators
from v2_1.src.telegram_notifier import TelegramNotifier


def position(direction=1, qty='1'):
    return ActivePosition('ETHUSDT', direction, D(qty), D(qty), D(100),
        D(90 if direction == 1 else 110), D(4), D(100), D(100), 0,
        original_entry=D(100), entry_fees=D(qty)*D('.06'))


def engine(direction=1, **kwargs):
    e = CoreV2Engine(**kwargs)
    e.open_positions['ETHUSDT'] = position(direction)
    e.realized_equity -= D('.06')
    return e


@pytest.mark.parametrize('direction', [1, -1])
def test_add_cash_ledger_and_weighted_price(direction):
    e = engine(direction)
    mark = D(100)+direction*D(8)
    e.process_price_event(1, {'ETHUSDT':mark})
    p = e.open_positions['ETHUSDT']
    add_price = mark*(1+direction*e.slippage)
    assert p.current_qty == D('1.5')
    assert p.entry_price == (D(100)+D('.5')*add_price)/D('1.5')
    exit_mark = p.current_stop-direction*D(1)
    e.process_price_event(2, {'ETHUSDT':exit_mark})
    exit_price = exit_mark*(1-direction*e.slippage)
    expected = D(300)-D('.06')-D('.5')*add_price*e.taker_fee
    expected += direction*((exit_price-D(100))+D('.5')*(exit_price-add_price))
    expected -= D('1.5')*exit_price*e.taker_fee
    assert abs(e.realized_equity-expected)<D('1e-20')
    assert abs(e.closed_trades[0].net_pnl-(expected-D(300)))<D('1e-20')


def test_old_low_never_triggers_new_live_stop():
    e = engine()
    e.open_positions['ETHUSDT'].lowest_price = D(91)
    e.process_price_event(1, {'ETHUSDT':D(108)})
    e.process_price_event(2, {'ETHUSDT':D(109)})
    assert 'ETHUSDT' in e.open_positions


@pytest.mark.parametrize('direction', [1, -1])
def test_partial_once_restart_and_final_ledger(direction):
    e = engine(direction, partial_tp_ratio=D('.35'))
    mark = D(100)+direction*D(15)
    e.process_price_event(1, {'ETHUSDT':mark})
    assert e.open_positions['ETHUSDT'].current_qty == D('.65')
    assert e.open_positions['ETHUSDT'].partial_taken
    saved = e.to_dict()
    restored = CoreV2Engine(partial_tp_ratio=D('.35'))
    restored.load_from_dict(saved)
    restored.process_price_event(2, {'ETHUSDT':mark})
    assert restored.open_positions['ETHUSDT'].current_qty == D('.65')
    restored._reduce('ETHUSDT', D('.65'), mark, 3, 'TEST')
    assert abs(restored.realized_equity-D(300)-restored.closed_trades[0].net_pnl)<D('1e-20')


def test_two_additions_cannot_cross_portfolio_cap():
    e = engine(portfolio_leverage=D('.7'))
    other = deepcopy(e.open_positions['ETHUSDT'])
    other.symbol = 'BTCUSDT'
    e.open_positions['BTCUSDT'] = other
    e.process_price_event(1, {'ETHUSDT':D(108),'BTCUSDT':D(108)})
    assert all(not p.is_pyramided for p in e.open_positions.values())


def test_halt_latched_after_recovery_and_restore():
    e = engine()
    e.realized_equity = D(240)
    e.process_price_event(1, {'ETHUSDT':D(99)})
    assert e.trading_halted and not e.open_positions
    restored = CoreV2Engine()
    restored.load_from_dict(e.to_dict())
    restored.realized_equity = D(400)
    restored.process_price_event(2, {})
    assert restored.trading_halted


@pytest.mark.parametrize('bad', ['NaN','Infinity','-1','0'])
def test_hostile_prices_do_not_mutate(bad):
    e = engine()
    before = e.to_dict()
    with pytest.raises(ValueError):
        e.process_price_event(1, {'ETHUSDT':D(bad)})
    assert e.to_dict() == before


def test_snapshot_load_transactional():
    e = engine()
    before = e.to_dict()
    bad = deepcopy(before)
    bad['realized_equity'] = '999'
    bad['open_positions']['ETHUSDT']['current_qty'] = 'NaN'
    with pytest.raises(ValueError):
        e.load_from_dict(bad)
    assert e.to_dict() == before


@pytest.mark.parametrize('step', ['0','-1','NaN','Infinity'])
def test_invalid_lot_rules_rejected(step):
    r = KellyRiskManager().calculate_position_size(D(300), D(100), D(90), D('.01'), D(step))
    assert not r.allowed


def test_small_price_retains_all_digits():
    c = MultiCoinScanner().evaluate_symbol_bar('X', .0045459,.0044,.0040,.0043,.0042,.000072,200,100)
    assert c.close_price == D('.0045459') and c.atr == D('.000072')


def test_adx_opt_in_blocks_low_or_missing_strength():
    scanner = MultiCoinScanner(min_adx=20)
    args = ('X',110,100,90,102,99,4,200,100)
    assert scanner.evaluate_symbol_bar(*args, adx=19) is None
    assert scanner.evaluate_symbol_bar(*args) is None
    assert scanner.evaluate_symbol_bar(*args, adx=25)


def test_indicators_causal_under_future_perturbation():
    rng = np.random.default_rng(123)
    close = 100+rng.normal(size=500).cumsum()
    df = pd.DataFrame(dict(timestamp=np.arange(500)*3600000,open=close,
        high=close+1,low=close-1,close=close,volume=np.ones(500)*100))
    a = prepare_symbol_indicators(df)
    df.loc[351:, ['close','high','low','volume']] *= 5
    b = prepare_symbol_indicators(df)
    pd.testing.assert_frame_equal(a.iloc[:351],b.iloc[:351])


def test_ws_delta_confirm_order_and_overflow():
    s = MarketStream(['ETHUSDT'], capacity=2)
    s.ingest(dict(topic='tickers.ETHUSDT',ts=1,data={'lastPrice':'100'}))
    s.ingest(dict(topic='tickers.ETHUSDT',ts=2,data={'fundingRate':'.01'}))
    s.ingest(dict(topic='kline.60.ETHUSDT',ts=3,data=[{'confirm':False}]))
    assert s.events.qsize() == 1
    s.ingest(dict(topic='kline.60.ETHUSDT',ts=4,data=[{'confirm':True}]))
    assert s.events.get_nowait()[0] == 'price'
    assert s.events.get_nowait()[0] == 'bar'
    for i in range(3):
        s.ingest(dict(topic='tickers.ETHUSDT',ts=i,data={'lastPrice':'100'}))
    assert s.overflow.is_set()


def test_atomic_write_failure_preserves_old_snapshot(tmp_path):
    p = tmp_path/'state.json'
    atomic_json(p, {'cash':'300'})
    with patch('v2_1.src.persistence.os.replace', side_effect=OSError('disk fault')):
        with pytest.raises(OSError):
            atomic_json(p, {'cash':'200'})
    assert json.loads(p.read_text()) == {'cash':'300'}
    assert list(tmp_path.iterdir()) == [p]


def test_disabled_telegram_no_network():
    with patch.dict('os.environ', {}, clear=True), patch('requests.post') as post:
        assert TelegramNotifier().notify({'kind':'TEST'}) is False
        post.assert_not_called()


def test_seeded_2000_tick_accounting_identity():
    rng = random.Random(291)
    for i in range(100):
        direction = rng.choice([-1,1])
        e = engine(direction, partial_tp_ratio=D('.35'))
        last = D(100)
        for j in range(20):
            last += D(str(rng.uniform(-3,3)))
            e.process_price_event(j+1, {'ETHUSDT':last})
        if e.open_positions:
            e._reduce('ETHUSDT', e.open_positions['ETHUSDT'].current_qty,last,30,'TEST')
        assert abs(e.realized_equity-D(300)-sum(t.net_pnl for t in e.closed_trades))<D('1e-20')


def signal_bar():
    return dict(open=100,high=112,low=99,close=110,upper=100,lower=90,
                ema_fast=102,ema_slow=99,atr=4,volume=200,volume_sma=100)


def test_missing_next_open_never_creates_position():
    e = CoreV2Engine()
    e.process_bar_event(1, {'ETHUSDT':signal_bar()}, {})
    assert not e.open_positions


def test_duplicate_bar_does_not_reenter_or_charge_fees():
    e = CoreV2Engine()
    e.process_bar_event(1, {'ETHUSDT':signal_bar()}, {'ETHUSDT':D(110)})
    before = e.to_dict()
    e.process_bar_event(1, {'ETHUSDT':signal_bar()}, {'ETHUSDT':D(110)})
    assert before == e.to_dict()


def test_gap_stop_fills_at_adverse_open_not_future_open():
    e = engine()
    b = dict(open=80,high=95,low=75,close=90,atr=4)
    e.process_bar_event(1, {'ETHUSDT':b}, {'ETHUSDT':D(92)})
    assert e.closed_trades[0].exit_price == D(80)*(1-e.slippage)


def test_funding_idempotent_across_restart():
    e = engine()
    e.process_funding_event(8, {'ETHUSDT':D('.001')}, {'ETHUSDT':D(100)})
    before = e.realized_equity
    restored = CoreV2Engine()
    restored.load_from_dict(e.to_dict())
    restored.process_funding_event(8, {'ETHUSDT':D('.001')}, {'ETHUSDT':D(100)})
    assert restored.realized_equity == before
    restored._reduce('ETHUSDT',D(1),D(100),9,'TEST')
    assert abs(restored.realized_equity-D(300)-restored.closed_trades[0].net_pnl)<D('1e-20')


def test_legacy_archive_loads_without_changing_cash():
    data = engine().to_dict()
    data.pop('schema_version')
    for p in data['open_positions'].values():
        for k in ('original_entry','entry_fees','realized_partial','funding_pnl','partial_taken'):
            p.pop(k)
    e = CoreV2Engine()
    e.load_from_dict(data)
    assert e.realized_equity == D(data['realized_equity'])
    assert 'ETHUSDT' in e.open_positions


def test_runtime_signal_requires_quote_after_bar_close():
    from v2_1.src.paper_trader_v2 import PaperTraderV2
    from unittest.mock import Mock
    import time
    trader = PaperTraderV2.__new__(PaperTraderV2)
    trader.symbols = ['ETHUSDT']
    ts = int(time.time()*1000)//3600000*3600000-3600000
    trader.batches = {ts:{'ETHUSDT':signal_bar()}}
    trader.ready = True
    trader.engine = Mock()
    trader.get_live_prices = lambda: {'ETHUSDT':D(110)}
    trader.price_server_times = {'ETHUSDT':ts+3599999}
    with patch('src.paper_trader_v2.time.time', return_value=(ts+3600001)/1000):
        trader._signals()
        trader.engine.process_bar_event.assert_not_called()
        trader.price_server_times['ETHUSDT'] = ts+3600001
        trader._signals()
        trader.engine.process_bar_event.assert_called_once()
        assert not trader.batches


def test_rest_instruments_all_pages_and_repeated_cursor_guard():
    from v2_1.src.bybit_client import BybitClient
    from unittest.mock import Mock
    client = BybitClient()
    client.request = Mock(side_effect=[{'result':{'list':[{'symbol':'A'}],'nextPageCursor':'next'}},
        {'result':{'list':[{'symbol':'B'}]}}])
    assert [p['symbol'] for p in client.get_instruments_info()] == ['A','B']
    client.request = Mock(return_value={'result':{'list':[],'nextPageCursor':'same'}})
    with pytest.raises(RuntimeError):
        client.get_instruments_info()

