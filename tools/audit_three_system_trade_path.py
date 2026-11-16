#!/usr/bin/env python3
"""READ-ONLY operational proof: market data -> authorized trade path -> ledgers.

No network/API calls, orders, resets, DB writes, imports from trading engines, or
changes to R2/Native/Shadow units. Python stdlib only. Output avoids credentials.
"""
import argparse
import collections
import datetime as dt
import json
from pathlib import Path
import sqlite3
import subprocess
import time

UTC = dt.timezone.utc
HOUR = 3_600_000
ROOT = Path("/opt/bybit-shadow")
DATA = ROOT / "data"
REPO = ROOT / "bybit-alpha-bot-SAM"
SERVICES = {
    "native": "bybit-shadow-native.service",
    "shadow": "bybit-shadow-realistic.service",
    "r2": "bybit-shadow-r2.service",
}
RUNNER_NAMES = {"native": "run_native_paper", "shadow": "run_shadow_paper"}


def run(*args):
    try:
        return subprocess.run(args, check=False, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, text=True, timeout=9).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def json_file(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def utc_time(ms):
    try:
        return dt.datetime.fromtimestamp(int(ms)/1000, tz=UTC).isoformat()
    except (ValueError, OverflowError, OSError, TypeError):
        return None


def iso_age(value):
    try:
        parsed=dt.datetime.fromisoformat(str(value).replace("Z","+00:00"))
        if parsed.tzinfo is None:
            return None
        return round((dt.datetime.now(UTC)-parsed).total_seconds())
    except (ValueError,TypeError):
        return None


def read_only_db(path):
    """SQLite read-only URI; avoid immutable=1 (would hide active WAL writes)."""
    if not path.is_file():
        return None
    conn=sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True,timeout=3)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


def table_exists(db, table):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def count(db, table):
    if not table_exists(db, table):
        return None
    return db.execute('SELECT count(*) FROM "'+table+'"').fetchone()[0]


def last_json(db, table):
    if not table_exists(db,table):
        return {}
    row=db.execute('SELECT state_json FROM "'+table+'" ORDER BY journal_id DESC LIMIT 1').fetchone()
    if not row:
        return {}
    try:
        return json.loads(row[0])
    except (ValueError,TypeError):
        return {}


def unit_status(name, kind):
    output=run("systemctl", "show", name, "-p",
               "ActiveState", "-p", "MainPID", "-p", "NRestarts",
               "-p", "ControlGroup", "--no-pager")
    values=dict(s.split("=",1) for s in output.splitlines() if "=" in s)
    cg=values.get("ControlGroup","").lstrip("/")
    path=Path("/sys/fs/cgroup")/cg/"cgroup.procs" if cg else None
    pids=set()
    if path is not None:
        try:
            pids={int(i) for i in path.read_text().split() if i.isdigit()}
        except OSError:
            pass
    pid=values.get("MainPID","")
    if pid.isdigit() and int(pid)>0:
        pids.add(int(pid))
    runners=[]
    for i in sorted(pids):
        try:
            cmd=(Path("/proc")/str(i)/"cmdline").read_bytes().split(b"\0")
            argv=[v.decode("utf-8",errors="replace") for v in cmd if v]
        except OSError:
            continue
        if kind in RUNNER_NAMES and any(RUNNER_NAMES[kind] in a for a in argv):
            runners.append({"pid":i, "schedule_active_cli": "--schedule-active" in argv,
                            "t0_arg":next((argv[j+1] for j,a in enumerate(argv[:-1]) if a=="--t0-utc"),None),
                            "telemetry_file":next((argv[j+1] for j,a in enumerate(argv[:-1]) if a=="--telemetry-file"),None)})
    return {"active":values.get("ActiveState"),"pid":values.get("MainPID"),
            "restarts_since_service_start":values.get("NRestarts"),
            "runner_processes":runners}


def telemetry(kind, unit):
    paths=[]
    for p in unit.get("runner_processes",[]):
        if p.get("telemetry_file"):
            paths.append(Path(p["telemetry_file"]))
    aliases={"native": ["telemetry_native.json","native_telemetry.json"],
             "shadow": ["telemetry_shadow.json","shadow_telemetry.json"]}
    for name in aliases.get(kind,[]):
        paths.extend([DATA/name, REPO/"reports"/name])
    try:
        paths += [p for p in DATA.glob("*.json") if "telemetry" in p.name.lower() and kind in p.name.lower()]
    except OSError:
        pass
    found=[]
    for p in dict.fromkeys(paths):
        obj=json_file(p)
        if isinstance(obj,dict) and ("schedule_active" in obj or "data_freshness_status" in obj):
            try: age=round(time.time()-p.stat().st_mtime)
            except OSError:age=None
            found.append((p,age,obj))
    if not found:
        return {"found":False}
    p,age,obj=min(found,key=lambda x:abs(x[1]) if x[1] is not None else 1e12)
    whitelist=("schedule_active","status","data_freshness_status","valuation_status",
               "is_active","positions_count","initial_capital_usdt","equity_usdt",
               "net_pnl_usdt","cohort_id","t0_utc","error_count","last_update_iso")
    out={k:obj[k] for k in whitelist if k in obj}
    out.update({"found":True,"file":str(p),"file_age_sec":age,
                "update_age_sec":iso_age(obj.get("last_update_iso"))})
    return out


def latest_bar_report(bar_map,now_ms):
    expected=(now_ms//HOUR-1)*HOUR
    age_in_hour=now_ms%HOUR
    count_latest=sum(int(x)>=expected for x in bar_map.values() if x is not None)
    max_b=max((int(x) for x in bar_map.values() if x is not None),default=None)
    return {"symbols_seen":len(bar_map),"symbols_caught_up":count_latest,
            "expected_closed_open_utc":utc_time(expected),
            "latest_recorded_open_utc":utc_time(max_b),
            "publication_grace":age_in_hour<180_000,
            "missing_symbols":sorted([s for s,t in bar_map.items() if t is None or int(t)<expected])[:25]}


def native_report(now_ms):
    path=DATA/"native_ledger.db"
    r={"database_present":path.is_file(),"database":str(path)}
    try:
        db=read_only_db(path)
        if db is None:return r
        with db:
            st=last_json(db,"native_state_journal")
            r["state_snapshots"]=count(db,"native_state_journal")
            r["bar_journal_rows"]=count(db,"native_journal")
            r["dedup_rows"]=count(db,"native_dedup")
            r["closed_trades"]=count(db,"native_trades")
            r["missed_opportunities"]=count(db,"native_missed_opportunities")
            r["latest_snapshot_at"]=st.get("saved_at_iso")
            bm=st.get("last_processed_bar_ts") or {}
            r["bars"]=latest_bar_report(bm,now_ms)
            e=st.get("engine",{})
            g=st.get("range_engine",{})
            r["trend_open_positions"]=len(e.get("open_positions") or {})
            r["range_open_positions"]=len(g.get("open_positions") or {})
            if table_exists(db,"native_missed_opportunities"):
                r["missed_reasons"]=dict(db.execute(
                  "SELECT reason,COUNT(*) FROM native_missed_opportunities GROUP BY reason ORDER BY COUNT(*) DESC LIMIT 8"))
            if table_exists(db,"native_journal"):
                row=db.execute("SELECT iso_time FROM native_journal ORDER BY journal_id DESC LIMIT 1").fetchone()
                r["last_bar_journal_time"]=row[0] if row else None
    except (OSError,sqlite3.Error,ValueError,TypeError) as exc:r["read_error"]=type(exc).__name__+": "+str(exc)[:130]
    return r


def shadow_report(now_ms):
    path=DATA/"shadow_ledger.db"
    r={"database_present":path.is_file(),"database":str(path)}
    try:
        db=read_only_db(path)
        if db is None:return r
        with db:
            r["state_snapshots"]=count(db,"shadow_state_journal")
            r["dedup_rows"]=count(db,"shadow_dedup_events")
            r["execution_event_rows"]=count(db,"shadow_execution_events")
            st=last_json(db,"shadow_state_journal")
            r["latest_snapshot_at"]=st.get("saved_at_utc")
            positions=st.get("shadow_portfolio") or {}
            r["shadow_open_positions"]=len(positions.get("positions") or {})
            r["closed_trades_in_state"]=len(positions.get("closed_trades") or [])
            r["rejected_orders_in_state"]=positions.get("rejected_orders_count")
            if table_exists(db,"shadow_dedup_events"):
                bars=dict(db.execute("SELECT symbol, MAX(exchange_ts) FROM shadow_dedup_events GROUP BY symbol"))
                r["bars"]=latest_bar_report(bars,now_ms)
            if table_exists(db,"shadow_execution_events"):
                r["event_types"]=dict(db.execute(
                    "SELECT event_type, COUNT(*) FROM shadow_execution_events GROUP BY event_type ORDER BY COUNT(*) DESC LIMIT 15"))
                r["execution_statuses"]=dict(db.execute(
                    "SELECT execution_status, COUNT(*) FROM shadow_execution_events GROUP BY execution_status"))
    except (OSError,sqlite3.Error,ValueError,TypeError) as exc:r["read_error"]=type(exc).__name__+": "+str(exc)[:130]
    return r


def r2_report():
    root=DATA/"cryptolab"
    r={"root_exists":root.is_dir()}
    for name in ("HEARTBEAT.json","COHORT.json","START_RECEIPT.json"):
        data=json_file(root/name)
        if isinstance(data,dict):
            r[name]={"status":data.get("status"),
                     "t0_utc":data.get("t0_utc"),
                     "at":data.get("at")}
            if name=="COHORT.json":
                r["cohort_t0_utc"]=data.get("t0_utc")
            if name=="HEARTBEAT.json":
                r["heartbeat_age_sec"]=iso_age(data.get("at"))
    try:
        db=read_only_db(root/"observer.sqlite")
        if db is not None:
            with db:
                r["accounts"]=count(db,"accounts")
                r["daily_decisions"]=count(db,"decisions")
                r["events"]=count(db,"events")
                if table_exists(db,"decisions"):
                    fields=[x[1] for x in db.execute("PRAGMA table_info(decisions)")]
                    if "day" in fields:
                        row=db.execute("SELECT MAX(day) FROM decisions").fetchone()
                        r["last_decision_day"]=row[0] if row else None
    except (OSError,sqlite3.Error,ValueError) as exc:r["read_error"]=type(exc).__name__+": "+str(exc)[:130]
    return r


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json",action="store_true",help="JSON only")
    opts=ap.parse_args()
    now_ms=int(time.time()*1000)
    result={"audit":"THREE_SYSTEM_TRADE_PATH_READ_ONLY","now_utc":utc_time(now_ms),
            "expected_closed_bar":utc_time((now_ms//HOUR-1)*HOUR),
            "services":{k:unit_status(v,k) for k,v in SERVICES.items()}}
    result["telemetry"]={k:telemetry(k,result["services"][k]) for k in ("native","shadow")}
    result["ledgers"]={"native":native_report(now_ms),"shadow":shadow_report(now_ms),"r2":r2_report()}
    warnings=[]
    for name in ("native","shadow"):
        u=result["services"][name];t=result["telemetry"][name];ledger=result["ledgers"][name]
        if u.get("active")!="active":
            warnings.append(name.upper()+"_SERVICE_INACTIVE")
        cli=u.get("runner_processes") or []
        if cli and all(not p["schedule_active_cli"] for p in cli):
            warnings.append(name.upper()+"_TRADE_GATE_FALSE_ON_RUNNING_PROCESS")
        if t.get("file_age_sec") is not None and t["file_age_sec"]<=120 and t.get("schedule_active") is False:
            warnings.append(name.upper()+"_TRADE_GATE_FALSE_IN_FRESH_TELEMETRY")
        if not t.get("found"):
            warnings.append(name.upper()+"_TELEMETRY_UNVERIFIED")
        elif t.get("file_age_sec") is not None and t["file_age_sec"]>120:
            warnings.append(name.upper()+"_TELEMETRY_STALE")
        bars=ledger.get("bars") or {}
        if bars.get("symbols_seen",0)<18:
            warnings.append(name.upper()+"_LESS_THAN_18_SYMBOLS_WITH_PERSISTED_CLOSED_BARS")
        elif not bars.get("publication_grace") and bars.get("symbols_caught_up",0)<18:
            warnings.append(name.upper()+"_HOUR_BAR_CATCHUP_LAG")
    if result["ledgers"]["r2"].get("accounts")!=24:
        warnings.append("R2_24_ACCOUNTS_UNVERIFIED")
    result["findings"]=warnings
    result["interpretation"]="Service ACTIVE and BAR_PROCESSED do not prove a trade signal. Zero trades can be normal under selective rules; distinguish DISABLED/GAP versus NO_SIGNAL with these facts."
    print(json.dumps(result,ensure_ascii=False,indent=2,default=str))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
