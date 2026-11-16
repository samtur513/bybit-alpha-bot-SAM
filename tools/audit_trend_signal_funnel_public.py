#!/usr/bin/env python3
"""One-shot public-data signal funnel audit, never a live/paper trading runner.

Exactly one Bybit public linear 1h kline request per of the configured 18
symbols, pace >=2s; no account keys, no orders, no trade-state/DB writes.
Reports indicative offline scanner candidates under production static filters.
It is NOT an event-sourced replay of the running engine, not a backtest, and
not evidence of a fill/profit.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT=Path("/opt/bybit-shadow/bybit-alpha-bot-SAM")
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))
import numpy as np
import pandas as pd
from v3_2.src.regime_classifier import MarketRegimeClassifier
from v3.src.multi_scanner import MultiCoinScanner
from v3_2.src.config import DEFAULT_SYMBOLS

HOUR=3_600_000
COHORT_T0="2026-11-16T00:00:00Z"


def fetch_public_klines(symbol, limit=800, timeout=14):
    params=urlencode({"category":"linear","symbol":symbol,"interval":"60","limit":limit})
    request=Request("https://api.bybit.com/v5/market/kline?"+params,
                    headers={"User-Agent":"Research-Only-TORG-SignalAudit/1.0"},
                    method="GET")
    with urlopen(request,timeout=timeout) as response:
        body=response.read(1_800_001)
        if len(body)>1_800_000:
            raise ValueError("unexpected large API response")
    obj=json.loads(body)
    if obj.get("retCode")!=0:
        raise RuntimeError(f"Bybit retCode={obj.get('retCode')} (no retry)")
    records=[]
    for k in obj.get("result",{}).get("list",[]):
        records.append({"timestamp":int(k[0]),
                        "open":float(k[1]),"high":float(k[2]),"low":float(k[3]),
                        "close":float(k[4]),"volume":float(k[5]),"turnover":float(k[6])})
    return pd.DataFrame.from_records(records)


def indicators(data, classifier):
    if data.empty:
        return data
    df=classifier.prepare_indicators(data)
    df["upper"]=df["high"].shift(1).rolling(120).max()
    df["lower"]=df["low"].shift(1).rolling(120).min()
    df["ema_fast"]=df["close"].ewm(span=50,adjust=False).mean()
    df["ema_slow"]=df["close"].ewm(span=200,adjust=False).mean()
    return df


def funnel_reason(bar, scanner):
    needed=("close","upper","lower","ema_fast","ema_slow","atr","volume","volume_sma","adx")
    try:
        d={k:float(bar[k]) for k in needed}
    except (KeyError,ValueError,TypeError):
        return "INVALID_INDICATORS",None
    if not all(math.isfinite(v) for v in d.values()) or min(d["close"],d["atr"],d["volume_sma"])<=0 or d["volume"]<0:
        return "INVALID_INDICATORS",None
    if d["adx"]<scanner.min_adx:
        return "ADX_BELOW_18",None
    if d["volume"]/d["volume_sma"]<scanner.min_volume_ratio:
        return "RVOL_BELOW_1_2",None
    if d["close"]>d["upper"]:
        if d["ema_fast"]<=d["ema_slow"]:
            return "LONG_EMA_DISAGREES",None
        return "LONG_CANDIDATE",1
    if d["close"]<d["lower"]:
        if d["ema_fast"]>=d["ema_slow"]:
            return "SHORT_EMA_DISAGREES",None
        return "SHORT_CANDIDATE",-1
    return "NO_120H_DONCHIAN_BREAKOUT",None


def analyze_bars(frames, t0_ms, now_ms, recent_days=30):
    scanner=MultiCoinScanner(min_adx=18.0,min_volume_ratio=1.2)
    start_hist=now_ms-recent_days*24*HOUR
    by_bucket={"live_since_T0":Counter(),"past_30d":Counter()}
    candidates={"live_since_T0":[],"past_30d":[]}
    regimes={"live_since_T0":Counter(),"past_30d":Counter()}
    classifier=MarketRegimeClassifier()
    for symbol,raw in frames.items():
        if raw.empty: continue
        df=indicators(raw.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True),classifier)
        for _,row in df.iterrows():
            ts=int(row["timestamp"])
            if ts+HOUR>now_ms: continue
            reg=classifier.classify_bar(
                symbol=symbol,adx=float(row.get("adx",float("nan"))),
                bb_mid=float(row.get("bb_mid",float("nan"))),
                bb_upper=float(row.get("bb_upper",float("nan"))),
                bb_lower=float(row.get("bb_lower",float("nan"))),
                bb_width_pctl=float(row.get("bb_width_pctl",float("nan"))),
                kc_upper=float(row.get("kc_upper",float("nan"))),
                kc_lower=float(row.get("kc_lower",float("nan"))),
                rsi=float(row.get("rsi",float("nan"))),
                atr=float(row.get("atr",float("nan"))),
                volume=float(row.get("volume",float("nan"))),
                volume_sma=float(row.get("volume_sma",float("nan"))))
            reason, direction=funnel_reason(row,scanner)
            for bucket in ("past_30d","live_since_T0"):
                if bucket=="live_since_T0" and ts+HOUR<t0_ms:continue
                if bucket=="past_30d" and ts+HOUR<start_hist:continue
                by_bucket[bucket][reason]+=1
                regimes[bucket][reg.regime.value]+=1
                if direction is not None:
                    # This verifies funnel and scanner agree, not that a portfolio could fill.
                    cand=scanner.evaluate_symbol_bar(
                        symbol=symbol,close=float(row["close"]),
                        upper_channel=float(row["upper"]),lower_channel=float(row["lower"]),
                        ema_fast=float(row["ema_fast"]),ema_slow=float(row["ema_slow"]),
                        atr=float(row["atr"]),volume=float(row["volume"]),
                        volume_sma=float(row["volume_sma"]),adx=float(row["adx"]))
                    if cand is None or cand.direction!=direction:
                        raise AssertionError(f"signal scanner parity mismatch: {symbol} {ts}")
                    candidates[bucket].append({
                      "symbol":symbol,"bar_close_utc":datetime.fromtimestamp((ts+HOUR)/1000,tz=timezone.utc).isoformat(),
                      "direction":"LONG" if direction==1 else "SHORT",
                      "rvol":round(float(row["volume"])/float(row["volume_sma"]),2),
                      "adx":round(float(row["adx"]),1)})
    return {"signal_funnel":{k:dict(v) for k,v in by_bucket.items()},
            "regimes":{k:dict(v) for k,v in regimes.items()},
            "trend_candidates":{k:len(v) for k,v in candidates.items()},
            "sample_trend_candidates":{k:v[-20:] for k,v in candidates.items()}}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--t0-utc",default=COHORT_T0)
    p.add_argument("--max-symbols",type=int,default=18)
    a=p.parse_args()
    try:
        t0=datetime.fromisoformat(a.t0_utc.replace("Z","+00:00"))
        if t0.tzinfo is None or t0.utcoffset().total_seconds()!=0: raise ValueError("T0 must be UTC")
        now_ms=int(time.time()*1000)
        frames={}
        errors={}
        syms=list(DEFAULT_SYMBOLS[:a.max_symbols])
        for i,sym in enumerate(syms):
            if i:time.sleep(2.2)
            try:
                data=fetch_public_klines(sym)
                data=data[data.timestamp+HOUR<=now_ms]
                frames[sym]=data
            except Exception as exc:
                errors[sym]=type(exc).__name__+": "+str(exc)[:130]
                if "10006" in str(exc):break
        report=analyze_bars(frames,int(t0.timestamp()*1000),now_ms)
        print(json.dumps({"audit":"PUBLIC_OHLCV_RESEARCH_ONLY","time_utc":datetime.fromtimestamp(now_ms/1000,tz=timezone.utc).isoformat(),
            "source":"Bybit public linear 1h klines, 1 request/symbol >=2.2s pacing",
            "symbol_requested":len(syms),"symbol_received":len(frames),
            "errors":errors,"timeframe":"1h",
            "warning":"Not a live decision replay and not a backtest. No orderbook, portfolio state, BTC filter, pending range order fills, execution costs or trade PnL evaluated.",
            **report},ensure_ascii=False,indent=2))
    except Exception as exc:
        print(json.dumps({"audit":"PUBLIC_OHLCV_RESEARCH_ONLY","error":type(exc).__name__+": "+str(exc)[:180]}))
        return 1
    return 0 if len(frames)==len(syms) else 2


if __name__=="__main__":
    raise SystemExit(main())
