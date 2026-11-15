"""Offline resource probe against archived public controls; no economic experiment or network."""
from pathlib import Path
from urllib.parse import urlparse,parse_qs
import json,gzip,zipfile,time,datetime,statistics,math,os,platform
from core import signal,SYMS,COSTS
from execution import validate_market,trade_account,valuation
R=Path(__file__).resolve().parent

def peak_rss():
 if os.name!='nt':
  import resource
  n=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
  return int(n if platform.system()=='Darwin' else n*1024)
 import ctypes
 from ctypes import wintypes
 class PMC(ctypes.Structure):
  _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)]+[(n,ctypes.c_size_t) for n in ['PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage']]
 info=PMC();info.cb=ctypes.sizeof(info)
 assert ctypes.windll.psapi.GetProcessMemoryInfo(wintypes.HANDLE(-1),ctypes.byref(info),info.cb)
 return int(info.PeakWorkingSetSize)

def main():
 rec=[json.loads(x) for x in (R/'evidence/control_raw/RECEIPTS.jsonl').read_text().splitlines()]
 latest={};sizes=[]
 with zipfile.ZipFile(R/'evidence/CONTROL_RAW_RESPONSES.zip') as z:
  for r in rec:
   b=z.read(r['file']);sizes.append(len(b));q=parse_qs(urlparse(r['url']).query);key=(urlparse(r['url']).path,q.get('symbol',[''])[0])
   latest[key]=(json.loads(gzip.decompress(b)),r)
 markets=[];cl=[]
 for sym in SYMS:
  meta,_=latest[('/v5/market/instruments-info',sym)]
  book,r=latest[('/v5/market/orderbook',sym)]
  received=int(datetime.datetime.fromisoformat(r['available_at']).timestamp()*1000)
  markets.append(validate_market(meta['result']['list'][0],book['result'],received))
  candles,_=latest[('/v5/market/kline',sym)]
  rows=sorted(candles['result']['list'],key=lambda a:int(a[0]))
  cl.append([float(row[4]) for row in rows])
 import numpy as np
 sig=signal(np.array(cl).T);start=time.perf_counter();cpu=time.process_time();iterations=250
 for _ in range(iterations):
  for strategy in ['BASELINE','R2']:
   for cap in [300,1000,10000]:
    for cost in COSTS:
     for policy in ['MODEL_C5','OBSERVED_BOOK']:
      cash,qty,_=trade_account(float(cap),[0.,0.,0.],sig[strategy],markets,cost,policy)
      valuation(cash,qty,markets,cost,policy)
 elapsed=time.perf_counter()-start;processor=time.process_time()-cpu
 requests=3*1440+3*24+18;block=4096
 # Depth50 control sizes overestimate the normal level1 payload; filesystem overhead matters.
 allocated=max(block,math.ceil(max(sizes)/block)*block)
 receipt_max=max(len(json.dumps(r).encode())+1 for r in rec)
 raw_daily=requests*(allocated+receipt_max)
 out=dict(kind='OFFLINE_CONTROL_RESOURCE_PROBE_NOT_VPS_OR_SUSTAINED_LOAD_TEST',host=platform.system(),python=platform.python_version(),iterations=iterations,virtual_account_evaluations=iterations*24,wall_seconds=elapsed,cpu_seconds=processor,cpu_seconds_per_24_account_batch=processor/iterations,peak_rss_bytes=peak_rss(),observed_control_signals=sig['eligible'].tolist(),raw_control_responses=len(rec),gzip_max_bytes=max(sizes),gzip_median_bytes=statistics.median(sizes),receipt_max_bytes=receipt_max,planning_requests_per_day=requests,planning_raw_files_183_days=requests*183,filesystem_allocation_assumed_bytes=block,raw_plus_receipt_daily_estimate_bytes=raw_daily,raw_plus_receipt_183day_estimate_bytes=raw_daily*183,exclusions=['no network latency/TLS CPU','no sustained daemon or Linux test','SQLite/WAL/CSV/logs/backups not included in disk estimate','flat control signals are not an active rebalance stress benchmark','filesystem inode overhead and outage error storms are not bounded'])
 (R/'LOCAL_RESOURCE_PROFILE.json').write_text(json.dumps(out,indent=2)+'\n',encoding='utf-8');print(json.dumps(out))
if __name__=='__main__':main()
