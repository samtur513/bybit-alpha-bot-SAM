"""Independent source/ledger/time audit. Does not import strategy or execution."""
from pathlib import Path
from collections import defaultdict
import csv,gzip,json,hashlib,datetime,math,statistics,importlib.util,zipfile
import numpy as np
R=Path(__file__).resolve().parent
def read(name):
 with (gzip.open(R/name,'rt',encoding='utf-8') if name.endswith('.gz') else (R/name).open(encoding='utf-8')) as f:return list(csv.DictReader(f))
def old():
 spec=importlib.util.spec_from_file_location('prior_read_only_audit',R.parent/'t01_profit_rank/audit.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 count=m.verify_old()
 for line in (R.parent/'t01_profit_rank/SHA256SUMS.txt').read_text().splitlines():
  h,p=line.split('  ',1);assert hashlib.sha256((R.parent/'t01_profit_rank'/p).read_bytes()).hexdigest()==h;count+=1
 return count
def source():
 p=R/'history';a=json.loads((p/'AVAILABILITY.json').read_text())
 if a['status']!='PASS':return dict(status='BLOCKED')
 by=defaultdict(dict);responses=0;closed_requests=0
 for line in (p/'raw/RECEIPTS.jsonl').read_text().splitlines():
  r=json.loads(line);raw=p/'raw'/r['file']
  if not(p/'RAW_RESPONSES.zip').exists():compressed=raw.read_bytes()
  else:
   with zipfile.ZipFile(p/'RAW_RESPONSES.zip') as archive:compressed=archive.read(r['file'])
  b=gzip.decompress(compressed);assert hashlib.sha256(b).hexdigest()==r['sha256']
  o=json.loads(b);sym=o['result']['symbol'];assert o['result']['category']=='spot' and o['retCode']==0
  for z in o['result']['list']:
   ts=int(z[0]);values=list(map(float,z[1:5]))
   assert 1638316800000<=ts<1704067200000
   if ts in by[sym]:assert by[sym][ts]==values
   by[sym][ts]=values
  responses+=1
 with np.load(p/'SPOT_2022_2023.npz',allow_pickle=False) as z:
  t=z['hour_t'];assert t.min()==27305280 and t.max()<28401120
  x=z['ohlc'];checks=0
  for j,sym in enumerate(['BTCUSDT','ETHUSDT','SOLUSDT']):
   assert sorted(by[sym])==list(t*60000)
   for k,ts in enumerate(t):assert np.array_equal(x[k,:,j],by[sym][int(ts)*60000]);checks+=4
 return dict(status='PASS',responses=responses,unique_hours_per_symbol=len(t),ohlc_values_checked=checks,oos2026_requests=closed_requests)
def ledger():
 with np.load(R/'history/SPOT_2022_2023.npz',allow_pickle=False) as z:t=z['hour_t'];x=z['ohlc']
 events=defaultdict(list)
 for z in read('HISTORICAL_TRADES.csv.gz'):
  key=(z['capital'],z['cost'],z['strategy'],z['day']);events[key].append(z)
 sig={(z['day'],z['symbol']):z for z in read('HISTORICAL_SIGNALS.csv')}
 state={};maxerr=0.;n=0
 for z in read('HISTORICAL_EQUITY.csv.gz'):
  key=(z['capital'],z['cost'],z['strategy']);cash,q=state.get(key,(float(z['capital']),np.zeros(3)))
  day=int((np.datetime64(z['date'])-np.datetime64('1970-01-01'))/np.timedelta64(1,'m'));fee=.001;slip=.0002 if z['cost']=='BASE' else .0005
  px=x[np.searchsorted(t,day+60),0];nav_before=cash+q@px
  ww=np.array([float(sig[str(day),sym]['r2_weight' if z['strategy']=='R2' else 'baseline_weight']) for sym in ['BTCUSDT','ETHUSDT','SOLUSDT']]);expected=nav_before*ww/(px*(1+fee+slip))
  for ev in sorted(events[key+(str(day),)],key=lambda a:(int(a['time']),a['side']=='BUY',a['symbol'])):
   j=['BTCUSDT','ETHUSDT','SOLUSDT'].index(ev['symbol']);tm=int(ev['time']);gross=float(ev['gross_qty']);p=float(ev['reference_price'])
   assert abs(float(ev['target_net'])-(0. if ev['reason']=='TERMINAL_TECHNICAL_LIQUIDATION' else expected[j]))<1e-6
   assert tm>=day+60 and tm<28401120 and abs(x[np.searchsorted(t,tm),0,j]-p)<1e-6
   if ev['side']=='BUY':q[j]+=gross*(1-fee);delta=-gross*p*(1+slip)
   else:q[j]-=gross;delta=gross*p*(1-slip)*(1-fee)
   assert abs(delta-float(ev['cash_delta']))<1e-5;cash+=delta;assert cash>=-1e-5
  observed=np.array([float(z['qty_BTC']),float(z['qty_ETH']),float(z['qty_SOL'])])
  err=max(abs(cash-float(z['cash'])),float(abs(q-observed).max()));maxerr=max(maxerr,err);assert err<1e-5
  idx=np.searchsorted(t,day+1380);p=x[idx,0 if z['date']=='2023-12-31' else 3]
  assert abs((cash+q@p)-float(z['nav']))<1e-5
  assert min(q)>-1e-8;state[key]=(cash,q);n+=1
 return dict(status='PASS',daily_balances=n,accounts=len(state),max_cash_qty_error=maxerr)
def geometry():
 with np.load(R/'history/SPOT_2022_2023.npz',allow_pickle=False) as z:t=z['hour_t'];cl=z['ohlc'][23::24,3]
 out=[]
 for d in range(31,len(cl)):
  score=[];e=[]
  for j in range(3):
   vals=[math.log(float(cl[k,j])/float(cl[k-1,j])) for k in range(d-28,d)]
   vol=statistics.stdev(vals)*math.sqrt(28);m=math.log(float(cl[d-1,j])/float(cl[d-29,j]));score.append(m/vol if vol>0 else float('nan'));e.append(bool(cl[d-1,j]>cl[d-29,j]))
  w=[1/3 if a else 0 for a in e]
  if sum(e)==3:
   ids=sorted(range(3),key=lambda j:(not math.isfinite(score[j]),-score[j],['BTCUSDT','ETHUSDT','SOLUSDT'][j]))[:2];w=[.5 if j in ids and math.isfinite(score[j]) else 0 for j in range(3)]
  for j,sym in enumerate(['BTCUSDT','ETHUSDT','SOLUSDT']):
   out.append(dict(day=int(t[d*24]),symbol=sym,eligible=e[j],r2_score=score[j],baseline_weight=1/3 if e[j] else 0,r2_weight=w[j],decision_seconds=int(t[d*24]*60+5),available_kind='MODELED_CLOSE_PLUS_5S_NOT_OBSERVED'))
 with (R/'HISTORICAL_SIGNALS.csv').open('w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(out[0]));w.writeheader();w.writerows(out)
 return dict(status='PASS',signal_symbol_rows=len(out),formula='independent math.log/statistics.stdev',last_input='preceding UTC daily close')
def main():
 out=dict(prior_sealed_files=old(),source=source(),signals=geometry(),ledger=ledger(),closed_oos2026_opened=False)
 (R/'INDEPENDENT_AUDIT.json').write_bytes((json.dumps(out,indent=2)+'\n').encode());print(json.dumps(out))
if __name__=='__main__':main()
