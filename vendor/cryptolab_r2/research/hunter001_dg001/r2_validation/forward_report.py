"""Read-only forward status. Never backfills or invents missing marks."""
from pathlib import Path
import argparse,sqlite3,json,datetime,csv
from collections import defaultdict
import numpy as np
from historical import inference
from public_data import UTC
def report(folder,out):
 folder=Path(folder);out=Path(out);out.mkdir(parents=True,exist_ok=True)
 db=sqlite3.connect('file:'+str((folder/'observer.sqlite').resolve())+'?mode=ro',uri=True)
 receipt=json.loads((folder/'FORWARD_START_RECEIPT.json').read_text());start=datetime.datetime.fromisoformat(receipt['prospective_start']);end=datetime.datetime.fromisoformat(receipt['minimum_end'])
 events=[(kind,at,json.loads(p)) for kind,at,p in db.execute('SELECT kind,at,payload FROM events ORDER BY at,id')]
 db.close()
 errors=sum(k in ['DATA_QUALITY','ERROR','MISSED_EXECUTION','MISSED_DECISION'] for k,_,_ in events);executions=sum(k=='EXECUTION' for k,_,_ in events)
 risk=sum(k=='RISK_BREACH' for k,_,_ in events)
 eq=defaultdict(list)
 for kind,at,z in events:
  if kind!='EQUITY' or z['record_type']!='FORWARD_EXECUTION':continue
  dt=datetime.datetime.fromisoformat(at)
  if not(start<=dt<end):continue
  key=(z['strategy'],z['capital'],z['cost'],z['policy']);eq[key].append((dt.date().isoformat(),z['mtm'],z['mdd'],z['risk_breached']))
 rows=[];boots=[]
 for cap in [300,1000,10000]:
  for cost in ['BASE','STRESS']:
   for policy in ['MODEL_C5','OBSERVED_BOOK']:
    a=eq.get(('R2',cap,cost,policy),[]);b=eq.get(('BASELINE',cap,cost,policy),[])
    if not a or [z[0] for z in a]!=[z[0] for z in b]:continue
    va=np.array([z[1] for z in a]);vb=np.array([z[1] for z in b])
    rows.append(dict(capital=cap,cost=cost,policy=policy,days=len(a),r2_mtm=va[-1],baseline_mtm=vb[-1],delta_dollars=va[-1]-vb[-1],r2_return=va[-1]/cap-1,baseline_return=vb[-1]/cap-1,r2_mdd=min(z[2] for z in a),status='PRELIMINARY_NOT_CONFIRMED'))
    if len(a)>=30:
     for block in [7,30]:boots.append(dict(capital=cap,cost=cost,policy=policy,**inference(va,vb,cap,block)))
 for policy in ['MODEL_C5','OBSERVED_BOOK']:
  for block in [7,30]:
   selected=sorted([z for z in boots if z['policy']==policy and z['block']==block],key=lambda z:z['p']);last=0
   for k,z in enumerate(selected):last=max(last,min(1,z['p']*(6-k)));z['p_holm']=last
 policy=json.loads((Path(__file__).resolve().parent/'DEPLOYMENT_STATUS.json').read_text(encoding='utf-8'))
 deployed=policy.get('deployment_authorized') is True and policy.get('status')=='DEPLOYED_VERIFIED'
 elapsed=datetime.datetime.now(UTC)>=end
 status=dict(at=datetime.datetime.now(UTC).isoformat(),prospective_start=start.isoformat(),minimum_end=end.isoformat(),elapsed_six_months=elapsed,executions=executions,data_errors_or_misses=errors,risk_breaches=risk,verdict='PENDING_SIX_MONTHS' if not elapsed else 'REQUIRES_FULL_DATA_EXECUTION_AND_RANDOM_CONTROL_AUDIT',real_capital='NOT_AUTHORIZED',original_oos2026='CLOSED',economic_results='NOT_ESTIMATED' if not rows else 'PRELIMINARY',turnover_matched_random='PRIMARY_HISTORICAL_COMPARABILITY_FAILED; NO CONFIRMATORY_RANDOM_CLAIM')
 if not deployed:status.update(verdict='NOT_DEPLOYED',deployment_status='NOT_DEPLOYED',schedule_active=False,prospective_start=None,minimum_end=None,archived_planned_start=start.isoformat(),elapsed_six_months=False)
 for name,rr in [('FORWARD_RESULTS.csv',rows),('FORWARD_UNCERTAINTY.csv',boots)]:
  with (out/name).open('w',encoding='utf-8',newline='') as f:
   w=csv.DictWriter(f,fieldnames=list(rr[0]) if rr else ['status']);w.writeheader();w.writerows(rr)
 (out/'FORWARD_STATUS.json').write_bytes((json.dumps(status,indent=2)+'\n').encode());print(json.dumps(status))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--data-dir',required=True);p.add_argument('--output',required=True);a=p.parse_args();report(a.data_dir,a.output)
