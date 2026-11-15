"""Single frozen temporal extension and prior-engine reconciliation, no network."""
from core import *
from replay import replay
from public_data import ms
import csv,gzip,io,statistics,hashlib
def emit(name,rows):
 rows=list(rows)
 if not rows:(ROOT/name).write_bytes(b'status\n');return
 stream=io.StringIO(newline='');w=csv.DictWriter(stream,fieldnames=list(rows[0]));w.writeheader()
 for r in rows:w.writerow({k:format(v,'.12g') if isinstance(v,(float,np.floating)) else v for k,v in r.items()})
 b=stream.getvalue().encode();(ROOT/name).write_bytes(gzip.compress(b,mtime=0) if name.endswith('.gz') else b)
def summary(r):
 v=r['nav'][0];cap=r['initial'];ret=v/np.r_[cap,v[:-1]]-1;prev=cap;a={}
 for year in sorted(set(x[:4] for x in r['dates'])):
  idx=np.flatnonzero(np.char.startswith(r['dates'],year));end=float(v[idx[-1]]);a['net_'+year]=end/prev-1;a['pnl_'+year]=end-prev;prev=end
 return dict(final_mtm=float(v[-1]),liquidatable_cash=float(r['cash'][0,-1]),dust=float(v[-1]-r['cash'][0,-1]),net=float(v[-1]/cap-1),**a,mdd=float(r['mdd'][0]),fees=float(r['fees'].sum()),slippage=float(r['slips'].sum()),turnover=float(r['turn'].sum()),avg_exposure=float(r['exposure'][0]),executed=int(r['executed'][0]),skipped=int(r['skipped'][0]),sharpe=float(ret.mean()/ret.std(ddof=1)*np.sqrt(365)),risk_gate='FAIL' if r['mdd'][0]<-.30 else 'PASS')
def inference(a,b,cap,block):
 ra=a/np.r_[cap,a[:-1]]-1;rb=b/np.r_[cap,b[:-1]]-1;delta=ra-rb;n=len(delta);rng=np.random.default_rng(6111502+block);means=[];compound=[]
 for _ in range(20):
  starts=rng.integers(n,size=(250,(n+block-1)//block));idx=((starts[:,:,None]+np.arange(block))%n).reshape(250,-1)[:,:n]
  means.extend(delta[idx].mean(axis=1));compound.extend(np.prod(1+ra[idx],axis=1)-np.prod(1+rb[idx],axis=1))
 means=np.array(means);obs=float(delta.mean())
 return dict(block=block,daily_delta=obs,daily_ci_low=float(np.quantile(means,.025)),daily_ci_high=float(np.quantile(means,.975)),compound_delta=float(a[-1]/cap-b[-1]/cap),compound_ci_low=float(np.quantile(compound,.025)),compound_ci_high=float(np.quantile(compound,.975)),p=float((1+np.sum(means-obs>=obs))/5001))
def main():
 availability=json.loads((ROOT/'history/AVAILABILITY.json').read_text())
 if availability['status']!='PASS':print('HISTORY BLOCKED; no partial-period economics');return
 with np.load(ROOT/'history/SPOT_2022_2023.npz',allow_pickle=False) as z:
  t=z['hour_t'];assert t[0]==ms('2021-12-01T00:00:00Z')//60000 and t[-1]<ms('2024-01-01T00:00:00Z')//60000
  x=z['ohlc']
 days,close,e,s=factors(t,x);weights={k:targets(e,s,k) for k in ['BASELINE','R2']}
 start=ms('2022-01-01T00:00:00Z')//60000;end=ms('2024-01-01T00:00:00Z')//60000;ids=np.flatnonzero(days>=start)
 stats=[];daily=[];events=[];reject=[];boots=[];randoms=[];randompaths=[];attribution=[];months=[]
 for cap in [300,1000,10000]:
  for cost in COSTS:
   cache={}
   for strategy,w in weights.items():
    r=replay(t,x,w,cap,cost,5,start,end);cache[strategy]=r;meta=dict(capital=cap,cost=cost,strategy=strategy)
    stats.append(dict(**meta,**summary(r),three_eligible_fraction=float(np.mean(e[ids].sum(axis=1)==3))))
    for i,date in enumerate(r['dates']):
     daily.append(dict(**meta,date=date,nav=r['nav'][0,i],cash=r['cash'][0,i],qty_BTC=r['q'][0,i,0],qty_ETH=r['q'][0,i,1],qty_SOL=r['q'][0,i,2],fees=float(r['fees'][0,i].sum()),turnover=float(r['turn'][0,i].sum())))
    events.extend(dict(**meta,**z) for z in r['events']);reject.extend(dict(**meta,**z) for z in r['rejections'])
    prev=float(cap)
    for month in sorted(set(z[:7] for z in r['dates'])):
     idx=np.flatnonzero(np.char.startswith(r['dates'],month));val=float(r['nav'][0,idx[-1]]);months.append(dict(**meta,month=month,net=val/prev-1,pnl=val-prev));prev=val
    # Symbol contribution: price PnL minus actual fees/buffer by carrying actual quantities over each mark interval.
    for j,sym in enumerate(SYMS):
     flow=sum(z['cash_delta'] for z in r['events'] if z['symbol']==sym);residual=r['q'][0,-1,j]*x[-1,0,j]
     attribution.append(dict(**meta,symbol=sym,net_contribution=flow+residual,fees=float(r['fees'][0,:,j].sum()),slippage=float(r['slips'][0,:,j].sum()),turnover=float(r['turn'][0,:,j].sum())))
   for block in [7,30]:boots.append(dict(capital=cap,cost=cost,**inference(cache['R2']['nav'][0],cache['BASELINE']['nav'][0],cap,block)))
   rw=persistent_random(e,weights['R2']);rr=replay(t,x,rw,cap,cost,5,start,end,log=False)
   # Normalized turnover uses pretrade NAV at each01:00 decision reference.
   def norm_turn(r):
    prevq=np.concatenate([np.zeros((len(r['q']),1,3)),r['q'][:,:-1,:]],axis=1)
    prevcash=np.concatenate([np.full((len(r['cash']),1),float(cap)),r['cash'][:,:-1]],axis=1)
    price=x[(ids*24+1),0,:];nav=prevcash+(prevq*price[None]).sum(axis=2)
    return (r['turn'].sum(axis=2)/nav).sum(axis=1)
   rt=norm_turn(rr);target=float(norm_turn(cache['R2'])[0]);exposure=cache['R2']['exposure'][0]
   comparable=(abs(rt/target-1)<=.20)&(abs(rr['exposure']-exposure)<=.02)
   dist=rr['nav'][:,-1];score=cache['R2']['nav'][0,-1]
   for idx in range(1000):randompaths.append(dict(capital=cap,cost=cost,path=idx,final_mtm=float(dist[idx]),normalized_turnover=float(rt[idx]),average_exposure=float(rr['exposure'][idx]),turnover_exposure_matched=bool(comparable[idx])))
   emit('RANDOM_PATHS.csv',randompaths)
   randoms.append(dict(capital=cap,cost=cost,draws=1000,r2_final=float(score),median=float(np.median(dist)),ci_low=float(np.quantile(dist,.025)),ci_high=float(np.quantile(dist,.975)),random_at_least_r2=float(np.mean(dist>=score)),r2_normalized_turnover=target,random_median_normalized_turnover=float(np.median(rt)),comparable_fraction=float(np.mean(comparable)),comparability_gate='PASS' if comparable.mean()>=.90 else 'FAIL',matched_paths=int(comparable.sum()),matched_median=float(np.median(dist[comparable])) if comparable.any() else None,matched_at_least_r2=float(np.mean(dist[comparable]>=score)) if comparable.any() else None,matched_subset_status='DESCRIPTIVE_ONLY_PRIMARY_GATE_FAILED',random_mean_exposure=float(rr['exposure'].mean()),r2_exposure=float(exposure)))
   print(cap,cost,'done',flush=True)
 for block in [7,30]:
  rows=[z for z in boots if z['block']==block];order=sorted(rows,key=lambda z:z['p']);last=0.
  for rank,z in enumerate(order):last=max(last,min(1,z['p']*(6-rank)));z['p_holm']=last
 emit('HISTORICAL_RESULTS.csv',stats);emit('HISTORICAL_EQUITY.csv.gz',daily);emit('HISTORICAL_TRADES.csv.gz',events);emit('HISTORICAL_REJECTIONS.csv.gz',reject);emit('HISTORICAL_UNCERTAINTY.csv',boots);emit('MATCHED_RANDOM_BASELINE.csv',randoms);emit('HISTORICAL_COIN_ATTRIBUTION.csv',attribution);emit('HISTORICAL_MONTHLY.csv',months)
 # One-time fixed-quantity B&H benchmark and annual/symbol decomposition.
 bh=[]
 for cap in [300,1000,10000]:
  for cost,(fee,slip) in COSTS.items():
   first=np.searchsorted(t,start+60);last=np.searchsorted(t,end-60)
   cash,q,tr=rebalance(np.array([float(cap)]),np.zeros((1,3)),x[first,0],np.full((1,3),cap/3)/(x[first,0]*(1+fee+slip)),fee,slip,5,True)
   values=cash[0]+x[first:last+1,0]@q[0];peak=np.maximum.accumulate(np.r_[cap,values])[1:];mdd=float(min(0,(values/peak-1).min()))
   at2022=np.searchsorted(t,ms('2022-12-31T23:00:00Z')//60000);yearnav=float(cash[0]+q[0]@x[at2022,3])
   cash,q,tr2=rebalance(cash,q,x[last,0],np.zeros_like(q),fee,slip,5,True);final=float(cash[0]+q[0]@x[last,0])
   bh.append(dict(capital=cap,cost=cost,final_mtm=final,liquidatable_cash=float(cash[0]),net_2022=yearnav/cap-1,net_2023=final/yearnav-1,mdd=min(mdd,final/peak.max()-1)))
 emit('HISTORICAL_BUY_HOLD.csv',bh)
 availability['historical_run_performed']=True;(ROOT/'history/AVAILABILITY.json').write_bytes((json.dumps(availability,indent=2)+'\n').encode())
 # Reconcile generalized time adapter to immutable prior C5 benchmark.
 ref=list(csv.DictReader((ROOT.parent/'t01_profit_rank/RISK_COMPARISON.csv').open()));checks=[]
 tt,xx,_=frozen.load();dd,_,ee,ss=factors(tt,xx)
 for cap in [300,1000,10000]:
  for cost in COSTS:
   for strategy in weights:
    r=replay(tt,xx,targets(ee,ss,strategy),cap,cost,5,28401120,29453760,log=False)
    prior=next(z for z in ref if z['capital']==str(cap) and z['cost']==cost and z['strategy']==strategy and z['constraint']=='C5')
    error=float(r['nav'][0,-1])-float(prior['final_mtm']);assert abs(error)<1e-6
    checks.append(dict(capital=cap,cost=cost,strategy=strategy,final=r['nav'][0,-1],prior=prior['final_mtm'],error=error))
 emit('PARENT_RECONCILIATION.csv',checks)
if __name__=='__main__':main()
