import json,sqlite3,datetime,math
import numpy as np
import pytest
from core import *
from execution import *
from public_data import guard_kline,ms,validate_ohlc,Public,UTC
from observer import Store,Observer,process_lock
def market(j=0,bids=None,asks=None):
 s=SYMS[j];meta=dict(symbol=s,status='Trading',lotSizeFilter=dict(basePrecision=str(frozen.STEPS[j]),quotePrecision=str(frozen.QSTEPS[j]),minOrderAmt='5',maxMarketOrderQty='100000'))
 b=dict(s=s,b=[list(map(str,z)) for z in (bids or [(99.,1000.),(98.,1000.)])],a=[list(map(str,z)) for z in (asks or [(101.,1000.),(102.,1000.)])],ts=100000)
 return validate_market(meta,b,100500)
@pytest.mark.parametrize('cost',['BASE','STRESS'])
@pytest.mark.parametrize('cap',[300,1000,10000])
def test_frozen_model_execution_identical(cost,cap):
 m=[market(j) for j in range(3)];w=np.array([.5,.5,0.]);q=[0.,0.,0.]
 c,z,e=trade_account(cap,q,w,m,cost,'MODEL_C5')
 fee,slip=COSTS[cost];p=np.array([100.]*3);target=cap*w/(p*(1+fee+slip))
 cc,zz,tr=rebalance(np.array([float(cap)]),np.zeros((1,3)),p,target[None],fee,slip,5,True)
 assert c==pytest.approx(cc[0]) and np.allclose(z,zz[0])
 assert c>=0 and sum(z)*100+c<=cap
@pytest.mark.parametrize('cost',['BASE','STRESS'])
def test_observed_buy_fee_base_sell_fee_quote(cost):
 m=[market(j) for j in range(3)]
 c,q,e=trade_account(300,[0,0,0],[1/3]*3,m,cost,'OBSERVED_BOOK')
 fee,slip=COSTS[cost]
 assert c>=0 and all(qi>=0 for qi in q)
 for j,ev in enumerate(e):
  assert ev['fee_base']==pytest.approx(ev['gross_qty']*fee)
  assert q[j]==pytest.approx(ev['gross_qty']*(1-fee))
 c2,q2,ee=trade_account(c,q,[0,0,0],m,cost,'OBSERVED_BOOK')
 assert c2<300 and all(qi>=0 for qi in q2)
 for ev in ee:
  if ev['gross_qty']>0:assert ev['cash_delta']==pytest.approx(ev['gross_qty']*ev['execution_price']*(1-fee))
def test_insufficient_depth_rejects_not_fabricated_fill():
 m=[market(j,bids=[(99,.0001)],asks=[(101,.0001)]) for j in range(3)]
 c,q,e=trade_account(1000,[0,0,0],[1/3]*3,m,'BASE','OBSERVED_BOOK')
 assert c==1000 and q==[0.,0.,0.] and all(z['status']=='REJECT_INSUFFICIENT_DEPTH' for z in e)
def test_dust_retained_and_not_cash():
 m=[market(j) for j in range(3)]
 v=valuation(10,[.001,0,0],m,'BASE','OBSERVED_BOOK')
 assert v['liquidatable_cash']==10 and v['dust_mtm']==pytest.approx(.1)
def test_minimum_and_maximum_limits():
 m=[market(j) for j in range(3)]
 for z in m:z['limits']['maxMarketOrderQty']=.01
 c,q,e=trade_account(300,[0,0,0],[1/3]*3,m,'BASE','OBSERVED_BOOK')
 assert c==300 and all(z['status']=='REJECT_MAX_QUANTITY' for z in e)
@pytest.mark.parametrize('change',['stale','crossed','descending_asks','zero_qty','status','missing_precision'])
def test_invalid_market_rejected(change):
 m=market();meta=m['meta'];book=m['book'];received=100500
 if change=='stale':received=106000
 if change=='crossed':book['a'][0][0]='90'
 if change=='descending_asks':book['a'].reverse()
 if change=='zero_qty':book['b'][0][1]='0'
 if change=='status':meta['status']='Suspended'
 if change=='missing_precision':del meta['lotSizeFilter']['basePrecision']
 with pytest.raises((AssertionError,KeyError)):validate_market(meta,book,received)
def test_exact_28_completed_returns_and_zero_vol():
 cl=np.exp(np.arange(29)[:,None]*np.array([[.01,.02,-.01]]))
 s=signal(cl)
 assert np.array_equal(s['eligible'],np.array([True,True,False]))
 assert np.allclose(s['momentum'],np.log(cl[-1]/cl[0]))
 flat=signal(np.ones((29,3))*100)
 assert np.isnan(flat['score']).all() and flat['R2'].sum()==0
def test_signal_one_ulp_and_tie():
 cl=np.ones((29,3))*100;cl[-1,0]=np.nextafter(100.,np.inf)
 s=signal(cl);assert s['eligible'][0] and not s['eligible'][1]
@pytest.mark.parametrize('start,end',[('2026-01-01T00:00:00Z','2026-01-02T00:00:00Z'),('2025-12-01T00:00:00Z','2026-10-02T00:00:00Z')])
def test_closed_oos_guard(start,end):
 with pytest.raises(AssertionError):guard_kline(ms(start),ms(end))
def test_allowed_data_windows():
 guard_kline(ms('2021-12-01T00:00:00Z'),ms('2024-01-01T00:00:00Z')-1)
 guard_kline(ms('2026-10-01T00:00:00Z'),ms('2026-11-14T00:00:00Z')-1)
def test_no_private_or_post_endpoints(tmp_path):
 p=Public(tmp_path)
 with pytest.raises(AssertionError):p.get('/v5/order/create',category='spot',symbol='BTCUSDT')
def test_gap_duplicates_unconfirmed_boundaries():
 rows=[['0','1','1','1','1'],['60000','1','1','1','1']]
 assert len(validate_ohlc(rows,60000,0,120000)[0])==2
 for bad in [rows[:1],rows+[rows[0]],[['0','1','0','2','1']]]:
  with pytest.raises(AssertionError):validate_ohlc(bad,60000,0,120000)
def test_persistent_random_matches_switch_schedule_and_prefix():
 e=np.ones((30,3),bool);r2=np.tile([.5,.5,0.],(30,1));r2[10:20]=[.5,0,.5]
 w=persistent_random(e,r2,100,123)
 changes=np.any(w[:,1:]!=w[:,:-1],axis=2);expected=np.any(r2[1:]!=r2[:-1],axis=1)
 assert np.all(changes==expected) and np.allclose(w.sum(axis=2),1)
 assert np.array_equal(w[:,:20],persistent_random(e[:20],r2[:20],100,123))
def test_sqlite_restart_idempotent_and_atomic(tmp_path):
 st=Store(tmp_path);assert len(st.accounts())==24
 with st.db:st.event('one','TEST',dict(x=1))
 st2=Store(tmp_path);assert st2.has('one') and len(st2.accounts())==24
 with pytest.raises(RuntimeError):
  with st.db:
   st.event('rollback','TEST',{})
   st.db.execute("UPDATE accounts SET payload='{}'")
   raise RuntimeError()
 assert not st.has('rollback') and all('cash' in a for _,a in st.accounts())
 with pytest.raises(sqlite3.IntegrityError):
  with st.db:st.event('one','TEST',{})
def test_missed_decision_cannot_backfill(tmp_path,monkeypatch):
 import observer
 day=datetime.datetime(2026,11,15,tzinfo=UTC)
 monkeypatch.setattr(observer,'utc',lambda:day+datetime.timedelta(hours=2))
 ob=Observer(tmp_path);ob.last_risk=float('inf')
 monkeypatch.setattr(ob,'markets',lambda **kw:pytest.fail('No market fetch when missed'))
 ob.tick(day.isoformat())
 assert ob.store.has('DECISION:2026-11-15') and ob.store.has('EXECUTION:2026-11-15')
 assert not ob.store.decision('2026-11-15')
 assert all(a['cash']==a['capital'] for _,a in ob.store.accounts())
def test_process_lock_is_exclusive(tmp_path):
 first=process_lock(tmp_path)
 with pytest.raises((OSError,BlockingIOError)):process_lock(tmp_path)
 first.close()
def test_risk_breach_persistent_after_recovery(tmp_path):
 ob=Observer(tmp_path);m=[market(j) for j in range(3)]
 key,a=ob.store.accounts()[0];a.update(cash=0,qty=[1,0,0],peak=300)
 with ob.store.db:
  ob.store.db.execute('UPDATE accounts SET payload=? WHERE id=?',(json.dumps(a),key))
  ob.mark_accounts('one',m,'TEST',risk_only=True)
 a=dict(ob.store.accounts())[key]
 assert a['risk_breached'] and a['mdd']<-.3
 m[0]['mid']=1000
 with ob.store.db:ob.mark_accounts('two',m,'TEST',risk_only=True)
 assert dict(ob.store.accounts())[key]['risk_breached']
def test_model_and_observed_never_claim_fills():
 m=[market(j) for j in range(3)]
 for path in ['MODEL_C5','OBSERVED_BOOK']:
  _,_,e=trade_account(1000,[0,0,0],[1/3]*3,m,'BASE',path)
  assert all(z['status']=='ESTIMATED_NOT_OBSERVED_FILL' for z in e)


def test_public_receipt_memory_bounded_disk_complete(tmp_path,monkeypatch):
 import public_data
 class Response:
  status=200
  def __enter__(self):return self
  def __exit__(self,*args):pass
  def read(self,limit):return b'{"retCode":0,"time":1000,"result":{}}'
 monkeypatch.setattr(public_data,'urlopen',lambda *args,**kwargs:Response())
 client=Public(tmp_path)
 for _ in range(110):client.get('/v5/market/time')
 records=[json.loads(line) for line in (tmp_path/'RECEIPTS.jsonl').read_text().splitlines()]
 assert len(client.receipts)==100 and len(records)==110
 assert len(list(tmp_path.glob('*.json.gz')))==110
 assert [r['id'] for r in client.receipts]==[r['id'] for r in records[-100:]]


@pytest.mark.parametrize('policy',[None,{}, {'deployment_authorized':False}, {'deployment_authorized':True,'authorized_start_utc':'wrong'}])
def test_daemon_not_deployed_has_no_side_effects(tmp_path,monkeypatch,policy):
 import observer
 root=tmp_path/'code';root.mkdir()
 if policy is not None:(root/'DEPLOYMENT_STATUS.json').write_text(json.dumps(policy))
 monkeypatch.setattr(observer,'ROOT',root)
 monkeypatch.setattr(observer,'Observer',lambda *args:pytest.fail('No database or network initialization allowed'))
 runtime=tmp_path/'runtime'
 with pytest.raises(RuntimeError,match='DEPLOYMENT_NOT_AUTHORIZED'):observer.daemon(runtime,'2026-11-15T00:00:00+00:00')
 assert not runtime.exists()

def test_authorization_requires_exact_published_start(tmp_path,monkeypatch):
 import observer
 start='2027-01-01T00:00:00+00:00'
 (tmp_path/'DEPLOYMENT_STATUS.json').write_text(json.dumps(dict(deployment_authorized=True,authorized_start_utc=start)))
 monkeypatch.setattr(observer,'ROOT',tmp_path)
 assert observer.require_deployment_authorization(start)['deployment_authorized'] is True
 with pytest.raises(RuntimeError,match='DEPLOYMENT_NOT_AUTHORIZED'):observer.require_deployment_authorization('2027-01-02T00:00:00+00:00')

def test_forward_report_not_deployed_does_not_claim_active_schedule(tmp_path,monkeypatch):
 import forward_report,shutil
 for name in ['observer.sqlite','FORWARD_START_RECEIPT.json']:
  shutil.copy2(ROOT/'evidence/control_state'/name,tmp_path/name)
 forward_report.report(tmp_path,tmp_path/'report')
 status=json.loads((tmp_path/'report/FORWARD_STATUS.json').read_text())
 assert status['verdict']=='NOT_DEPLOYED'
 assert status['prospective_start'] is None and status['minimum_end'] is None
 assert status['executions']==0 and not status['schedule_active']
