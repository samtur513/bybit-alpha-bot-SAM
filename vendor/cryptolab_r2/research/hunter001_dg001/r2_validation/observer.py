"""Minimal public-data paper observer. No credentials, private API, orders or trading core."""
from public_data import *
from core import signal,COSTS
from execution import trade_account,validate_market,valuation
from concurrent.futures import ThreadPoolExecutor
import sqlite3,csv,os,argparse,calendar,math
import numpy as np
DEFAULT=ROOT.parent/'work/r2_observer_runtime'
START='2026-11-15T00:00:00+00:00'
def atomic(path,obj):
 path=Path(path);tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_bytes((json.dumps(obj,indent=2,allow_nan=False)+'\n').encode());os.replace(tmp,path)
class Store:
 def __init__(self,folder):
  self.folder=Path(folder);self.folder.mkdir(parents=True,exist_ok=True)
  self.db=sqlite3.connect(self.folder/'observer.sqlite',timeout=10)
  self.db.execute('PRAGMA journal_mode=WAL')
  self.db.executescript('CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,kind TEXT,at TEXT,payload TEXT); CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY,payload TEXT); CREATE TABLE IF NOT EXISTS decisions(day TEXT PRIMARY KEY,payload TEXT);')
  with self.db:
   for cap in [300,1000,10000]:
    for strategy in ['BASELINE','R2']:
     for cost in COSTS:
      for policy in ['MODEL_C5','OBSERVED_BOOK']:
       key=f'{strategy}:{cap}:{cost}:{policy}'
       self.db.execute('INSERT OR IGNORE INTO accounts VALUES (?,?)',(key,json.dumps(dict(strategy=strategy,capital=cap,cost=cost,policy=policy,cash=float(cap),qty=[0.,0.,0.],peak=float(cap),mdd=0.,risk_breached=False))))
 def has(self,id):return self.db.execute('SELECT 1 FROM events WHERE id=?',(id,)).fetchone() is not None
 def event(self,id,kind,payload):
  self.db.execute('INSERT INTO events VALUES (?,?,?,?)',(id,kind,utc().isoformat(),json.dumps(payload,allow_nan=False)))
 def decision(self,day): 
  row=self.db.execute('SELECT payload FROM decisions WHERE day=?',(day,)).fetchone()
  return json.loads(row[0]) if row else None
 def accounts(self):return [(k,json.loads(v)) for k,v in self.db.execute('SELECT id,payload FROM accounts ORDER BY id')]
 def export(self):
  mapping={'SNAPSHOT':'SIGNAL_AND_MARKET_SNAPSHOTS.csv','ORDER':'PAPER_ORDER_LEDGER.csv','EQUITY':'BASELINE_VS_R2_EQUITY.csv'}
  for kind,name in mapping.items():
   rows=[dict(event_id=k,recorded_at=a,**json.loads(p)) for k,a,p in self.db.execute('SELECT id,at,payload FROM events WHERE kind=? ORDER BY at,id',(kind,))]
   fields=sorted(set().union(*(z.keys() for z in rows))) if rows else {'SNAPSHOT':['event_id','recorded_at','record_type','symbol'],'ORDER':['event_id','recorded_at','strategy','capital','cost','policy','status'],'EQUITY':['event_id','recorded_at','strategy','capital','cost','policy','mtm']}
   path=self.folder/name;tmp=path.with_suffix('.csv.tmp')
   with tmp.open('w',encoding='utf-8',newline='') as f:
    w=csv.DictWriter(f,fieldnames=list(fields));w.writeheader()
    for z in rows:w.writerow({k:json.dumps(v) if isinstance(v,(list,dict)) else v for k,v in z.items()})
   os.replace(tmp,path)
class Observer:
 def __init__(self,folder=DEFAULT):
  self.store=Store(folder);self.api=Public(Path(folder)/'raw');self.meta_cache={};self.last_risk=0.;self.last_export=0.
 def markets(self,refresh=True,depth=50):
  def one(sym):
   cached=self.meta_cache.get(sym)
   if refresh or cached is None or time.monotonic()-cached[2]>3600:
    meta,mr=self.api.get('/v5/market/instruments-info',category='spot',symbol=sym);self.meta_cache[sym]=(meta,mr,time.monotonic())
   else:meta,mr,_=cached
   book,br=self.api.get('/v5/market/orderbook',category='spot',symbol=sym,limit=depth)
   assert meta['result']['category']=='spot' and len(meta['result']['list'])==1
   m=validate_market(meta['result']['list'][0],book['result'],int(datetime.datetime.fromisoformat(br['available_at']).timestamp()*1000))
   m['metadata_receipt']=mr;m['book_receipt']=br;return m
  with ThreadPoolExecutor(max_workers=3) as pool:out=list(pool.map(one,SYMS))
  now=utc().timestamp()*1000;timestamps=[z['book']['ts'] for z in out]
  assert max(timestamps)-min(timestamps)<=5000,'INTERSYMBOL_BOOK_SKEW'
  assert all(0<=now-z['book']['ts']<=5000 for z in out),'STALE_BOOK_AT_USE'
  return out
 def signals(self,day):
  end=int(day.timestamp()*1000);start=end-29*86400000
  guard_kline(start,end-1)
  def one(sym):
   o,r=self.api.get('/v5/market/kline',category='spot',symbol=sym,interval='D',start=start,end=end-1,limit=29)
   ts,x=validate_ohlc(o['result']['list'],86400000,start,end)
   assert int(ts[-1])+86400000<=utc().timestamp()*1000
   return [row[3] for row in x],r
  with ThreadPoolExecutor(max_workers=3) as pool:out=list(pool.map(one,SYMS))
  cl=np.array([z[0] for z in out]).T;sig=signal(cl)
  return sig,[z[1] for z in out]
 def snapshots(self,id,kind,day,markets,sig=None,candles=None):
  for j,m in enumerate(markets):
   z=dict(record_type=kind,decision_day=day.isoformat(),symbol=SYMS[j],book_event_at_ms=m['book']['ts'],available_at=m['book_receipt']['available_at'],ingested_at=m['book_receipt']['ingested_at'],rtt_ms=m['book_receipt']['rtt_ms'],bid=m['bids'][0][0],ask=m['asks'][0][0],bid_levels=len(m['bids']),ask_levels=len(m['asks']),book_sha256=m['book_receipt']['sha256'],metadata_sha256=m['metadata_receipt']['sha256'],limits=m['limits'],fill_claim='QUOTE_ESTIMATE_NOT_PROVED_FILL')
   if sig is not None:z.update(eligible=bool(sig['eligible'][j]),r2_score=float(sig['score'][j]) if math.isfinite(sig['score'][j]) else None,baseline_weight=float(sig['BASELINE'][j]),r2_weight=float(sig['R2'][j]),candle_sha256=candles[j]['sha256'],candle_available_at=candles[j]['available_at'])
   self.store.event(id+':'+SYMS[j],'SNAPSHOT',z)
 def control(self):
  day=utc().replace(hour=0,minute=0,second=0,microsecond=0);sig,receipts=self.signals(day);markets=self.markets();id='CONTROL:'+uuid.uuid4().hex
  # Functional replay is separate from prospective balances and order ledger.
  smoke=[]
  for strategy in ['BASELINE','R2']:
   for cost in COSTS:
    for policy in ['MODEL_C5','OBSERVED_BOOK']:
     for cap in [300,1000,10000]:
      cash,q,ev=trade_account(float(cap),[0.,0.,0.],sig[strategy],markets,cost,policy)
      val=valuation(cash,q,markets,cost,policy)
      assert cash>=0 and min(q)>=0 and val['mtm']<=cap+1e-6
      smoke.append(dict(strategy=strategy,capital=cap,cost=cost,policy=policy,test_orders=len(ev),estimated_cost=cap-val['mtm'],status='CONTROL_ONLY_NOT_FORWARD_RESULT'))
  with self.store.db:self.snapshots(id,'CONTROL_ONLY',day,markets,sig,receipts);self.store.event(id,'CONTROL',dict(status='PASS',smoke_accounts=len(smoke)))
  self.store.export();atomic(self.store.folder/'FUNCTIONAL_CHECK.json',dict(status='PASS',at=utc().isoformat(),control_id=id,accounts=smoke,forward_orders=0))
  return dict(status='PASS',control_id=id,snapshot_count=3,smoke_accounts=len(smoke))
 def decide(self,day):
  key='DECISION:'+day.date().isoformat()
  if self.store.has(key):return
  if utc()>day+datetime.timedelta(minutes=5,seconds=5):raise ValueError('MISSED_DECISION_WINDOW')
  sig,receipts=self.signals(day);markets=self.markets()
  assert utc()<=day+datetime.timedelta(minutes=5,seconds=5)
  data={k:v.tolist() for k,v in sig.items() if k in ['eligible','BASELINE','R2']}
  data.update(available_at=utc().isoformat(),day=day.isoformat())
  with self.store.db:
   self.store.db.execute('INSERT INTO decisions VALUES (?,?)',(day.date().isoformat(),json.dumps(data)))
   self.snapshots(key,'FORWARD_DECISION',day,markets,sig,receipts)
   self.store.event(key,'DECISION',data)
   self.mark_accounts(key,markets,'FORWARD_DECISION')
  self.store.export()
 def mark_accounts(self,key,markets,record_type,risk_only=False,persist=True):
  for account,a in self.store.accounts():
   val=valuation(a['cash'],a['qty'],markets,a['cost'],a['policy']) if not risk_only else dict(mtm=a['cash']+sum(a['qty'][j]*markets[j]['mid'] for j in range(3)),liquidation_not_estimated_from_shallow_risk_quote=True)
   breached_before=a['risk_breached'];a['peak']=max(a['peak'],val['mtm']);a['mdd']=min(a['mdd'],val['mtm']/a['peak']-1);a['risk_breached']=a['risk_breached'] or val['mtm']<a['peak']*.70
   self.store.db.execute('UPDATE accounts SET payload=? WHERE id=?',(json.dumps(a),account))
   if not breached_before and a['risk_breached']:self.store.event('RISK_BREACH:'+account,'RISK_BREACH',dict(account=account,at=utc().isoformat(),mdd=a['mdd'],real_capital_gate='NO_GO'))
   if persist:self.store.event(key+':EQUITY:'+account,'EQUITY',dict(record_type=record_type,**a,**val,real_capital_gate='NO_GO_RISK' if a['risk_breached'] else 'NOT_AUTHORIZED'))
 def execute(self,day):
  key='EXECUTION:'+day.date().isoformat()
  if self.store.has(key):return
  due=day+datetime.timedelta(hours=1)
  assert due<=utc()<=due+datetime.timedelta(seconds=60),'MISSED_EXECUTION_WINDOW'
  decision=self.store.decision(day.date().isoformat());assert decision is not None,'NO_VALID_DECISION'
  markets=self.markets();assert utc()<=due+datetime.timedelta(seconds=60),'EXECUTION_DATA_ARRIVED_LATE'
  with self.store.db:
   self.snapshots(key,'FORWARD_EXECUTION',day,markets)
   for account,a in self.store.accounts():
    w=decision[a['strategy']];before=dict(a)
    cash,q,events=trade_account(a['cash'],a['qty'],w,markets,a['cost'],a['policy'])
    for k,e in enumerate(events):
     reason='RESTORE_TARGET' 
     if e['symbol'] in SYMS:
      j=SYMS.index(e['symbol']);oldw=a.get('last_weights',[0.,0.,0.])
      if (w[j]>0)!=(oldw[j]>0):reason='TREND_OR_LEADER_CHANGE'
     self.store.event(key+':ORDER:'+account+':'+str(k),'ORDER',dict(record_type='FORWARD_VIRTUAL_ESTIMATE',strategy=a['strategy'],capital=a['capital'],cost=a['cost'],policy=a['policy'],reason=reason,**e))
    a.update(cash=cash,qty=q,last_weights=w);self.store.db.execute('UPDATE accounts SET payload=? WHERE id=?',(json.dumps(a),account))
   self.mark_accounts(key,markets,'FORWARD_EXECUTION')
   self.store.event(key,'EXECUTION',dict(status='ESTIMATES_RECORDED_NOT_ACTUAL_FILLS',at=utc().isoformat(),accounts=24))
  self.store.export()
 def tick(self,start):
  now=utc();first=datetime.datetime.fromisoformat(start).astimezone(UTC).replace(hour=0,minute=0,second=0,microsecond=0)
  today=now.replace(hour=0,minute=0,second=0,microsecond=0);day=first
  while day<=today:
   dk='DECISION:'+day.date().isoformat();ek='EXECUTION:'+day.date().isoformat()
   if now>=day+datetime.timedelta(seconds=5) and not self.store.has(dk):
    try:self.decide(day)
    except Exception as e:
     with self.store.db:self.store.event(dk,'MISSED_DECISION',dict(day=day.isoformat(),reason=str(e),no_backfill=True))
   if now>=day+datetime.timedelta(hours=1) and not self.store.has(ek):
    try:self.execute(day)
    except Exception as e:
     with self.store.db:self.store.event(ek,'MISSED_EXECUTION',dict(day=day.isoformat(),reason=str(e),no_backfill=True))
   day+=datetime.timedelta(days=1)
  # Observe BBO risk once a minute; hourly equity persistence keeps the service small.
  if now>=first and time.monotonic()-self.last_risk>=60:
   try:
    market=self.markets(refresh=False,depth=1);hourkey='RISK:'+now.strftime('%Y-%m-%dT%H')
    hourly=not self.store.has(hourkey)
    with self.store.db:
     self.mark_accounts(hourkey,market,'FORWARD_HOURLY_RISK_MARK',risk_only=True,persist=hourly)
     if hourly:
      self.snapshots(hourkey,'FORWARD_RISK_BBO',today,market)
      self.store.event(hourkey,'RISK_MARK',dict(at=utc().isoformat(),depth=1))
    self.last_risk=time.monotonic()
   except Exception as e:
    with self.store.db:self.store.event('RISK_MARK_MISSING:'+uuid.uuid4().hex,'DATA_QUALITY',dict(at=utc().isoformat(),reason=str(e),positive_capital_gate_blocked=True))
  if time.monotonic()-self.last_export>=3600:
   self.store.export();self.last_export=time.monotonic()
def process_lock(folder):
 lock=(Path(folder)/'process.lock').open('a+b');lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
 if os.name=='nt':
  import msvcrt;msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
 else:
  import fcntl;fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 return lock
def require_deployment_authorization(start):
 try:policy=json.loads((ROOT/'DEPLOYMENT_STATUS.json').read_text(encoding='utf-8'))
 except (OSError,ValueError) as e:raise RuntimeError('DEPLOYMENT_NOT_AUTHORIZED: missing or invalid deployment status') from e
 if policy.get('deployment_authorized') is not True or policy.get('authorized_start_utc')!=start:
  raise RuntimeError('DEPLOYMENT_NOT_AUTHORIZED: separate user permission and matching published future start required')
 return policy

def daemon(folder,start):
 require_deployment_authorization(start)
 ob=Observer(folder);lock=process_lock(folder);now=utc();planned=datetime.datetime.fromisoformat(start)
 assert planned>=datetime.datetime(2026,11,15,tzinfo=UTC)
 prior=Path(folder)/'FORWARD_START_RECEIPT.json'
 if not prior.exists() and now>=planned:raise RuntimeError('START_MISSED: preregister and publish a new FUTURE start; no backfill')
 if prior.exists():assert json.loads(prior.read_text())['prospective_start']==planned.isoformat(),'START_CHANGE_REQUIRES_NEW_PROTOCOL'
 check=json.loads((Path(folder)/'FUNCTIONAL_CHECK.json').read_text());assert check['status']=='PASS'
 codehash={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in ['core.py','execution.py','observer.py','public_data.py','PREREGISTRATION.json']}
 receipt=dict(code_sha256=codehash,protocol_sha256=codehash['PREREGISTRATION.json'],status='RUNNING_ARMED' if now<planned else 'RUNNING_FORWARD',process_started_at=now.isoformat(),prospective_start=planned.isoformat(),minimum_end=(planned.replace(year=planned.year+(planned.month+5)//12,month=(planned.month+5)%12+1)).isoformat(),pid=os.getpid(),host_kind='LOCAL_WINDOWS' if os.name=='nt' else 'VPS_OR_LINUX_UNVERIFIED_HOST',protocol_commit='7b6fed5ae5d72573e827f4e5c2cee419c183b445',real_orders=0,private_api_calls=0,control_id=check['control_id'],forward_observations=0)
 atomic(Path(folder)/'FORWARD_START_RECEIPT.json',receipt);heartbeats=0
 while not(Path(folder)/'STOP').exists():
  try:
   require_deployment_authorization(start)
   assert all(hashlib.sha256((ROOT/n).read_bytes()).hexdigest()==h for n,h in codehash.items()),'CODE_OR_PROTOCOL_CHANGED_FAIL_CLOSED'
   ob.tick(start)
  except Exception as e:
   with ob.store.db:ob.store.event('LOOP_ERROR:'+uuid.uuid4().hex,'ERROR',dict(error=str(e)))
   if 'CODE_OR_PROTOCOL_CHANGED' in str(e) or 'DEPLOYMENT_NOT_AUTHORIZED' in str(e):raise
  heartbeats+=1
  atomic(Path(folder)/'HEARTBEAT.json',dict(at=utc().isoformat(),pid=os.getpid(),heartbeats=heartbeats,prospective_start=start,status='ARMED' if utc()<planned else 'OBSERVING',forward_executions=ob.store.db.execute("SELECT count(*) FROM events WHERE kind='EXECUTION'").fetchone()[0],missed=ob.store.db.execute("SELECT count(*) FROM events WHERE kind LIKE 'MISSED%'").fetchone()[0]))
  time.sleep(15)
 atomic(Path(folder)/'STOPPED.json',dict(at=utc().isoformat(),pid=os.getpid(),reason='USER_STOP_FILE'))
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['control','daemon','export']);parser.add_argument('--data-dir',default=str(DEFAULT));parser.add_argument('--start',default=START);args=parser.parse_args()
 if args.mode=='daemon':daemon(args.data_dir,args.start)
 else:
  ob=Observer(args.data_dir)
  if args.mode=='control':print(json.dumps(ob.control()))
  else:ob.store.export()
