# Data quality

{
  "prior_sealed_files": 498,
  "source": {
    "status": "PASS",
    "responses": 66,
    "unique_hours_per_symbol": 18264,
    "ohlc_values_checked": 219168,
    "oos2026_requests": 0
  },
  "signals": {
    "status": "PASS",
    "signal_symbol_rows": 2190,
    "formula": "independent math.log/statistics.stdev",
    "last_input": "preceding UTC daily close"
  },
  "ledger": {
    "status": "PASS",
    "daily_balances": 8760,
    "accounts": 12,
    "max_cash_qty_error": 4.9221853259950876e-08
  },
  "closed_oos2026_opened": false
}

All3 official Spot inputs cover [2021-12-01,2024-01-01), no gaps/duplicates/invalid OHLC. 66 public responses,4,682,958 raw bytes,18,264hours/symbol. All219,168 OHLC values independently compared to raw responses; overlapping probes identical. Historical event times are observed exchange bar labels; publication availability in the past is UNKNOWN/MODELED, never OBSERVED. Forward HTTP receive times are observed, and separate from server/book event timestamps and raw ingested_at. Orderbook age<=5s, cross-symbol skew<=5s, no crossed/unsorted/nonpositive books.

135 regression tests PASS (37new+98prior). Published XML is evidence, not an economic result. The generalized replay matches all12 parent C5 capital/cost/strategy cases to<1e-6USD. All498 previous sealed files remain unchanged.

Forward controls verify the source and plumbing; there are0 prospective performance observations at publication. Observer NOT DEPLOYED; local control process stopped before prospective start. Current spectra do not establish historical filters; bid/ask snapshots do not establish actual fills. Primary random comparability fails and is explicitly retained.
