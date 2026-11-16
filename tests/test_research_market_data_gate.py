"""No-live-order regressions for research hourly candle rate gating."""
from pathlib import Path

from v3_2.src.runners.hourly_kline_gate import HourlyKlineGate

H = 3_600_000


def test_no_identical_hourly_kline_fetches_every_five_seconds():
    gate = HourlyKlineGate(interval_ms=H, after_close_delay_ms=10_000)
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    # 14:00 UTC, 13:00 bar is available after a 10-sec publication grace.
    boundary = 14 * H
    last = {symbol: 12 * H for symbol in symbols}
    requests = []
    for now in range(boundary, boundary + 10 * 60_000, 5000):
        for symbol in gate.due_symbols(symbols, last, now):
            requests.append((symbol, now))
            gate.record_attempt(symbol, now)
            last[symbol] = 13 * H
    assert len(requests) == 3, requests
    assert min(t for _, t in requests) == boundary + 10_000


def test_failure_cooldown_preserves_missing_bar_for_retry():
    gate = HourlyKlineGate(interval_ms=H, after_close_delay_ms=10_000,
                          retry_delay_ms=60_000)
    now = 14 * H + 10_000
    state = {"BTCUSDT": 12 * H}
    assert gate.due_symbols(["BTCUSDT"], state, now) == ["BTCUSDT"]
    gate.record_attempt("BTCUSDT", now)
    assert gate.due_symbols(["BTCUSDT"], state, now + 5000) == []
    assert gate.due_symbols(["BTCUSDT"], state, now + 59_000) == []
    assert gate.due_symbols(["BTCUSDT"], state, now + 60_000) == ["BTCUSDT"]
    # An authoritative processed timestamp stops further attempts.
    state["BTCUSDT"] = 13 * H
    assert gate.due_symbols(["BTCUSDT"], state, now + 61_000) == []


def test_historical_catchup_is_not_blocked_by_close_grace():
    gate = HourlyKlineGate(interval_ms=H, after_close_delay_ms=25_000)
    now = 14 * H + 2000
    assert gate.due_symbols(["ETHUSDT"], {"ETHUSDT": 11 * H}, now) == ["ETHUSDT"]
    # Only the immediately previous bar is withheld until publication grace.
    assert gate.due_symbols(["ETHUSDT"], {"ETHUSDT": 12 * H}, now) == []


def test_shadow_does_not_request_the_latest_bar_before_grace():
    shadow = HourlyKlineGate(interval_ms=H, after_close_delay_ms=25_000)
    native = HourlyKlineGate(interval_ms=H, after_close_delay_ms=10_000)
    state = {"BTCUSDT": 12 * H}
    assert shadow.due_symbols(["BTCUSDT"], state, 14 * H + 15_000) == []
    assert native.due_symbols(["BTCUSDT"], state, 14 * H + 15_000) == ["BTCUSDT"]


def test_18_symbol_batch_only_once_each_hour():
    gate = HourlyKlineGate(interval_ms=H, after_close_delay_ms=10_000)
    symbols = [f"TEST{i}USDT" for i in range(18)]
    state = {s: 12 * H for s in symbols}
    requests = 0
    for now in range(14 * H, 15 * H, 5000):
        for symbol in gate.due_symbols(symbols, state, now):
            gate.record_attempt(symbol, now)
            state[symbol] = 13 * H
            requests += 1
    assert requests == 18
    assert len(gate.due_symbols(symbols, state, 15 * H + 25_000)) == 18


def test_runner_integration_only_gates_kline_not_quotes():
    root = Path(__file__).resolve().parents[1]
    for runner in ["run_native_paper.py", "run_shadow_paper.py"]:
        src = (root / "v3_2/src/runners" / runner).read_text()
        assert "due_symbols = self._kline_gate.due_symbols(" in src
        assert "for sym in due_symbols:" in src
        assert "self._kline_gate.record_attempt(sym, now_srv)" in src
        assert "time.sleep(0.18)" in src
        # Live quote/MTM + funding APIs still execute per 5-second loop.
        assert "self.client.get_tickers_detail(category=\"linear\")" in src
        assert "self.poll_market_step()" in src
        assert "time.sleep(interval_sec)" in src
