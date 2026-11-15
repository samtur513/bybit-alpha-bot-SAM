"""Allowlisted read-only snapshot exporter. No HTTP server and no command input."""
from pathlib import Path
import datetime, json, os, sqlite3
UTC = datetime.timezone.utc
PROFILES = ["R2:300:BASE:OBSERVED_BOOK", "R2:300:STRESS:OBSERVED_BOOK", "BASELINE:300:BASE:OBSERVED_BOOK"]

def export(folder, output, now=None, quotes=None):
    now = now or datetime.datetime.now(UTC)
    root = Path(folder).resolve()
    target = Path(output).resolve()
    # A dashboard cannot overwrite any journal, code or configuration through export.
    if target.name != "telemetry.json" or target.parent != root:
        raise ValueError("TELEMETRY_OUTPUT_MUST_BE_RUNTIME_TELEMETRY_JSON")
    db = sqlite3.connect((root/"observer.sqlite").as_uri()+"?mode=ro",uri=True)
    try:
        db.execute("BEGIN")
        accounts = {k:json.loads(v) for k,v in db.execute("SELECT id,payload FROM accounts")}
        latest, totals, history = {}, {}, {k:[] for k in PROFILES}
        for eid,kind,at,payload in db.execute("SELECT id,kind,at,payload FROM events ORDER BY rowid"):
            z = json.loads(payload)
            if kind in ["EQUITY","ORDER"]:
                key = f"{z['strategy']}:{z['capital']}:{z['cost']}:{z['policy']}"
                if key not in PROFILES:
                    continue
                if kind == "EQUITY":
                    latest[key] = (at,z,eid.split(":EQUITY:")[0])
                    history[key].append(dict(at=at, equity_usdt=z["mtm"]))
                    history[key] = history[key][-512:]
                else:
                    t = totals.setdefault(key, dict(estimated_legs=0,rejected_legs=0,fees_usdt=0.,buffer_usdt=0.,spread_impact_usdt=0.,turnover_usdt=0.))
                    t["estimated_legs"] += z["gross_qty"] > 0
                    t["rejected_legs"] += z["status"].startswith("REJECT")
                    for dest,src in [("fees_usdt","fee_quote"),("buffer_usdt","buffer_cost"),("spread_impact_usdt","book_spread_impact")]:
                        t[dest] += z[src]
                    t["turnover_usdt"] += z["gross_qty"]*z["reference_mid"]
        counts = dict(db.execute("SELECT kind,count(*) FROM events GROUP BY kind").fetchall())
        last = db.execute("SELECT at,payload FROM events WHERE kind='DECISION' ORDER BY rowid DESC LIMIT 1").fetchone()
        decision = json.loads(last[1]) if last else None
        scores = {}
        for at,payload in db.execute("SELECT at,payload FROM events WHERE kind='SNAPSHOT' ORDER BY rowid DESC LIMIT 300"):
            z = json.loads(payload)
            if z.get("record_type") == "FORWARD_DECISION" and z["symbol"] not in scores:
                scores[z["symbol"]] = dict(score=z.get("r2_score"), eligible=z.get("eligible"), target_weight=z.get("r2_weight"))
        current = quotes.current(now) if quotes is not None else None
        profiles = []
        for key in PROFILES:
            a = accounts[key]
            mark = latest.get(key)
            age = (now-datetime.datetime.fromisoformat(mark[0])).total_seconds() if mark else None
            fresh = mark is not None and 0 <= age <= 120
            v = mark[1] if fresh else {}
            valuation_at = mark[0] if mark else None
            if quotes is not None:
                # Revalue current balances from one read-only DB snapshot and quote batch.
                # No historical mark, risk flag, target or trade is written/re-timestamped.
                v = {}
                if current is not None:
                    v["mtm"] = a["cash"] + sum(q*p for q,p in zip(a["qty"],current["prices"]))
                    valuation_at = current["at"]
                    fresh = True
                else:
                    fresh = False
            equity = v.get("mtm")
            profiles.append(dict(profile=key, initial_capital_usdt=300, cash_usdt=a["cash"],
                quantities=dict(zip(["BTCUSDT","ETHUSDT","SOLUSDT"],a["qty"])),
                equity_usdt=equity, net_pnl_usdt=equity-300 if equity is not None else None,
                net_return_pct=(equity/300-1)*100 if equity is not None else None,
                mdd_pct=a["mdd"]*100, risk_breached=a["risk_breached"],
                liquidatable_cash_usdt=v.get("liquidatable_cash"), dust_usdt=v.get("dust_mtm"),
                exposure_pct=(1-a["cash"]/equity)*100 if equity else None,
                cash_fraction_pct=a["cash"]/equity*100 if equity else None,
                valuation_at=valuation_at, valuation_status="FRESH" if fresh else "UNVALUED_OR_STALE",
                profit_factor=None, profit_factor_reason="LOT_REALIZATION_NOT_DEFINED",
                totals=totals.get(key,{}), equity_history=history[key]))
        values = {x["profile"]:x for x in profiles}
        ra,ba = values[PROFILES[0]],values[PROFILES[2]]
        paired = (ra["equity_usdt"] is not None and ba["equity_usdt"] is not None
                  and (current is not None if quotes is not None else latest[PROFILES[0]][2] == latest[PROFILES[2]][2]))
        cohort = json.loads((root/"COHORT.json").read_text())
        heartbeat = json.loads((root/"HEARTBEAT.json").read_text()) if (root/"HEARTBEAT.json").exists() else None
        heartbeat_age = (now-datetime.datetime.fromisoformat(heartbeat["at"])).total_seconds() if heartbeat else None
        obj = dict(schema_version="r2-shadow-telemetry/1", system="CRYPTOLAB_R2_SHADOW", generated_at=now.isoformat(),
            deployment_status="NOT_DEPLOYED" if not (root/"START_RECEIPT.json").exists() else "STARTED_UNVERIFIED",
            process_status=heartbeat["status"] if heartbeat and 0<=heartbeat_age<=45 else "NO_FRESH_HEARTBEAT",
            heartbeat_at=heartbeat["at"] if heartbeat else None,
            cohort=dict(t0_utc=cohort["t0_utc"], minimum_end_utc=cohort["minimum_end_utc"], schedule_active=(root/"START_RECEIPT.json").exists()),
            market="BYBIT_SPOT_LONG_CASH_NO_LEVERAGE", profiles=profiles,
            comparison=dict(r2_minus_t01_usdt=ra["equity_usdt"]-ba["equity_usdt"] if paired else None,
                r2_minus_t01_pp=ra["net_return_pct"]-ba["net_return_pct"] if paired else None,
                status="PAIRED_PRELIMINARY" if paired else "UNPAIRED_OR_UNVALUED"),
            last_signal_at=last[0] if last else None,
            signal=scores, target_weights=decision["R2"] if decision else None,
            missed_decisions=counts.get("MISSED_DECISION",0), missed_executions=counts.get("MISSED_EXECUTION",0),
            data_errors=counts.get("DATA_QUALITY",0)+counts.get("ERROR",0),
            real_orders=0, demo_orders=0, economic_verdict="INCONCLUSIVE_REAL_CAPITAL_NO_GO",
            fill_claim="QUOTE_ESTIMATE_NOT_PROVED_FILL")
    finally:
        db.close()
    tmp = target.with_suffix(".json.tmp")
    data = (json.dumps(obj,sort_keys=True,allow_nan=False)+"\n").encode()
    with tmp.open("wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp,target)
    return obj
