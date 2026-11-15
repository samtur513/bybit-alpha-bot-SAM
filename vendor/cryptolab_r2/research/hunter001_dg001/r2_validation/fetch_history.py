"""Bounded official-Spot availability inventory; full extension only if all probes pass."""
from public_data import *
import numpy as np
def main():
 out=ROOT/'history';out.mkdir(exist_ok=True);api=Public(out/'raw');start=ms('2021-12-01T00:00:00Z');end=ms('2024-01-01T00:00:00Z');step=3600000
 probes=[];ok=True
 for sym in SYMS:
  for label,a in [('warmup',start),('2022_start',ms('2022-01-01T00:00:00Z')),('2023_end',end-step)]:
   try:
    o,r=api.get('/v5/market/kline',category='spot',symbol=sym,interval='60',start=a,end=a+step-1,limit=1)
    ts,values=validate_ohlc(o['result']['list'],step,a,a+step)
    probes.append(dict(symbol=sym,label=label,start=a,rows=len(ts),status='PASS',receipt=r['id']))
   except Exception as e:probes.append(dict(symbol=sym,label=label,start=a,status='BLOCKED',reason=str(e)));ok=False
 status=dict(probes=probes,status='PROBES_PASS' if ok else 'BLOCKED',historical_run_performed=False,oos_price_requests=0)
 (out/'AVAILABILITY.json').write_bytes((json.dumps(status,indent=2)+'\n').encode())
 if not ok:print(json.dumps(status));return
 allx=[];lastt=None
 for sym in SYMS:
  rows=[]
  for a in range(start,end,step*1000):
   b=min(end,a+step*1000)
   o,r=api.get('/v5/market/kline',category='spot',symbol=sym,interval='60',start=a,end=b-1,limit=1000)
   rows+=o['result']['list']
   assert len(api.receipts)<=72 and sum(z['bytes'] for z in api.receipts)<=20*1024**2
  try:tt,xx=validate_ohlc(rows,step,start,end)
  except Exception as e:
   status.update(status='BLOCKED',reason=str(e),failed_symbol=sym);(out/'AVAILABILITY.json').write_bytes((json.dumps(status,indent=2)+'\n').encode());print(json.dumps(status));return
  if lastt is not None:assert tt==lastt
  lastt=tt;allx.append(np.asarray(xx));print(sym,len(tt),flush=True)
 np.savez_compressed(out/'SPOT_2022_2023.npz',hour_t=np.array(lastt,dtype=np.int64)//60000,ohlc=np.stack(allx,axis=2))
 status.update(status='PASS',hours_per_symbol=len(lastt),requests=len(api.receipts),raw_bytes=sum(z['bytes'] for z in api.receipts),source_npz_sha256=hashlib.sha256((out/'SPOT_2022_2023.npz').read_bytes()).hexdigest())
 (out/'AVAILABILITY.json').write_bytes((json.dumps(status,indent=2)+'\n').encode());print(json.dumps(status))
if __name__=='__main__':main()
