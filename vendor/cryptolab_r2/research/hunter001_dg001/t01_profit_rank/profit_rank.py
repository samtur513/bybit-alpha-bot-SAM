"""Offline spot scenario engine. Imports no account, order API or trading core."""
from pathlib import Path
import json,hashlib,csv
import numpy as np
ROOT=Path(__file__).resolve().parent
P=json.loads((ROOT/'PREREGISTRATION.json').read_text(encoding='utf-8'))
SYMS=P['symbols']; STEPS=np.array(P['base_steps']); QSTEPS=np.array(P['quote_steps'])
def writecsv(name,rows,fields=None):
 rows=list(rows)
 with (ROOT/name).open('w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=fields or list(rows[0]));w.writeheader();w.writerows(rows)
def dump(name,x):(ROOT/name).write_bytes((json.dumps(x,indent=2,allow_nan=False)+'\n').encode())
def load():
 ts=[];xx=[];receipts=[]
 for s in SYMS:
  p=ROOT.parent/'alpha_search/inputs'/f'{s}.npz'
  with np.load(p,allow_pickle=False) as z:t=z['hour_t'].copy();x=z['hour_values'][:,:4].copy()
  assert t[0]==28356480 and t[-1]==29453700 and np.all(np.diff(t)==60)
  assert np.isfinite(x).all() and (x>0).all()
  assert np.all(x[:,1]>=np.maximum(x[:,0],x[:,3])) and np.all(x[:,2]<=np.minimum(x[:,0],x[:,3]))
  ts.append(t);xx.append(x);receipts.append(dict(symbol=s,sha256=hashlib.sha256(p.read_bytes()).hexdigest(),hours=len(t),first=int(t[0]),last=int(t[-1])))
 assert all(np.array_equal(ts[0],t) for t in ts)
 return ts[0],np.stack(xx,axis=2),receipts
def factors(t,x):
 closes=x[23::24,3,:];days=t[::24];mom=np.full_like(closes,np.nan);vol=mom.copy();eligible=np.zeros_like(closes,bool)
 for d in range(29,len(days)):
  r=np.log(closes[d-28:d]/closes[d-29:d-1])
  mom[d]=np.log(closes[d-1]/closes[d-29]);eligible[d]=closes[d-1]>closes[d-29]
  vol[d]=r.std(axis=0,ddof=1)*np.sqrt(28)
 scores={'R1':mom,'R2':np.divide(mom,vol,out=np.full_like(mom,np.nan),where=vol>0),'R3':mom-mom[:,0,None]}
 return days,closes,eligible,scores
def targets(eligible,scores,kind,rng=None,excluded=None):
 e=eligible.copy()
 if excluded is not None:e[:,excluded]=False
 w=e.astype(float)/3
 if kind in ('BASELINE','EXTERNAL'):return w
 for d in np.flatnonzero(e.sum(axis=1)==3):
  ids=np.flatnonzero(e[d])
  if kind=='RANDOM':order=rng.permutation(ids)
  else:order=sorted(ids,key=lambda j:(not np.isfinite(scores[kind][d,j]),-scores[kind][d,j] if np.isfinite(scores[kind][d,j]) else 0,SYMS[j]))
  w[d]=0
  for j in order[:2]:
   if kind=='RANDOM' or np.isfinite(scores[kind][d,j]):w[d,j]=.5
 return w
def floor_step(a,step):
 # Never round upward across an exchange boundary.
 return np.floor(np.maximum(0,a)/step)*step
def rebalance(cash,q,price,target,fee,slip,minimum,discrete):
 """Vectorized batch accounts. All sales then common pro-rata buy budget."""
 cash=cash.copy();q=q.copy();initial_q=q.copy();p=np.asarray(price);before=cash+(q*p).sum(axis=1)
 gross_sell=np.maximum(q-target,0)
 if discrete:gross_sell=floor_step(gross_sell,STEPS)
 sv=gross_sell*p*(1-slip)
 sell_ok=(gross_sell>1e-14)&(sv>=minimum)
 sell_reject=(gross_sell>1e-14)&~sell_ok
 sold=np.where(sell_ok,gross_sell,0);sf=sold*p*(1-slip)*fee
 cash+=(sold*p*(1-slip)-sf).sum(axis=1);q-=sold
 want=np.maximum(target-q,0)/(1-fee)
 spend=want*p*(1+slip)
 scale=np.minimum(1,np.divide(cash,spend.sum(axis=1),out=np.ones_like(cash),where=spend.sum(axis=1)>0))
 spend*=scale[:,None]
 if discrete:spend=floor_step(spend,QSTEPS)
 buygross=spend/(p*(1+slip))
 if discrete:buygross=floor_step(buygross,STEPS)
 actual=buygross*p*(1+slip)
 buy_ok=(buygross>1e-14)&(actual>=minimum)
 buy_reject=(buygross>1e-14)&~buy_ok
 bought=np.where(buy_ok,buygross,0);bf=bought*fee
 cash-=(bought*p*(1+slip)).sum(axis=1);q+=bought-bf
 fees=sf+bf*p;slippage=(sold+bought)*p*slip;turn=(sold+bought)*p
 assert np.min(cash)>-1e-7 and np.min(q)>-1e-10
 cash=np.maximum(cash,0);q=np.maximum(q,0)
 assert np.allclose(cash+(q*p).sum(axis=1),before-fees.sum(axis=1)-slippage.sum(axis=1),rtol=1e-11,atol=1e-7)
 precision_reject=(np.abs(target-initial_q)>1e-14)&(sold==0)&(bought==0)&~sell_reject&~buy_reject
 return cash,q,dict(precision_reject=precision_reject,sold=sold,bought=bought,fees=fees,fee_base=bf,slippage=slippage,turn=turn,sell_reject=sell_reject,buy_reject=buy_reject,sell_requested=gross_sell,buy_requested=buygross)
def simulate(t,x,weights,capital,cost,minimum,log=True,year=None):
 """Weights [accounts,days,3] or [days,3]; daily snapshots + hourly risk."""
 if weights.ndim==2:weights=weights[None,:,:]
 n=len(weights);fee,slip=P['costs'][cost];discrete=minimum>0
 start=28401120 if year!=2025 else 28928160
 end=29453760 if year!=2024 else 28928160
 dayids=np.flatnonzero((t[::24]>=start)&(t[::24]<end));D=len(dayids)
 q=np.zeros((n,3));cash=np.full(n,float(capital));nav=np.empty((n,D));qs=np.empty((n,D,3));cs=np.empty((n,D));fees=np.zeros((n,D,3));slips=fees.copy();turns=fees.copy()
 executed=np.zeros(n,int);skipped=executed.copy();peak=np.full(n,float(capital));mdd=np.zeros(n);exp_sum=np.zeros(n);cash_sum=np.zeros(n);hours=0;events=[];rejections=[];lastw=np.zeros((n,3));prev_e=np.zeros((n,3),bool)
 def mark(pr):
  nonlocal peak,mdd,exp_sum,cash_sum,hours
  values=cash[:,None]+q@pr.T
  # pr has each chronological hourly reference open. No mark enters decisions.
  running=np.maximum.accumulate(np.concatenate([peak[:,None],values],axis=1),axis=1)[:,1:]
  mdd=np.minimum(mdd,np.min(values/running-1,axis=1));peak=np.maximum(peak,values.max(axis=1))
  expo=1-cash[:,None]/values
  exp_sum+=expo.sum(axis=1);cash_sum+=(cash[:,None]/values).sum(axis=1);hours+=len(pr)
 for k,d in enumerate(dayids):
  mark(x[d*24:d*24+1,0,:])
  price=x[d*24+1,0,:];w=weights[:,d,:]
  equity=cash+(q*price).sum(axis=1);target=equity[:,None]*w/(price*(1+fee+slip))
  qb=q.copy();cb=cash.copy()
  cash,q,tr=rebalance(cash,q,price,target,fee,slip,minimum,discrete)
  executed+=((tr['sold']>0)|(tr['bought']>0)).sum(axis=1)
  skipped+=(tr['sell_reject']|tr['buy_reject']|tr['precision_reject']).sum(axis=1)
  fees[:,k]=tr['fees'];slips[:,k]=tr['slippage'];turns[:,k]=tr['turn']
  if log:
   assert n==1
   for j,s in enumerate(SYMS):
    trend=(w[0,j]>0)!=(lastw[0,j]>0)
    reason='TREND_OR_SELECTION_CHANGE' if trend else 'RESTORE_TARGET_WEIGHT'
    for side,key,rej,request in [('SELL','sold','sell_reject','sell_requested'),('BUY','bought','buy_reject','buy_requested')]:
     gross=float(tr[key][0,j])
     base=dict(day=int(t[d*24]),time=int(t[d*24]+60),symbol=s,side=side,reason=reason,reference_price=float(price[j]),target_net=float(target[0,j]),qty_before=float(qb[0,j]),qty_after=float(q[0,j]))
     if gross>0:
      f=gross*fee*(price[j] if side=='BUY' else price[j]*(1-slip))
      delta=gross*price[j]*(1-slip)*(1-fee) if side=='SELL' else -gross*price[j]*(1+slip)
      events.append(dict(**base,gross_qty=gross,cash_delta=delta,fee_quote=f,fee_base=gross*fee if side=='BUY' else 0.,slippage_quote=gross*price[j]*slip))
     elif tr[rej][0,j]:
      rejections.append(dict(**base,requested_gross=float(tr[request][0,j]),rejection='BELOW_MODELED_MINIMUM'))
    raw=abs(float(target[0,j]-qb[0,j]))
    if raw>1e-14 and tr['sold'][0,j]==0 and tr['bought'][0,j]==0 and not(tr['sell_reject'][0,j] or tr['buy_reject'][0,j]):
     rejections.append(dict(day=int(t[d*24]),time=int(t[d*24]+60),symbol=s,side='BUY' if target[0,j]>qb[0,j] else 'SELL',reason=reason,reference_price=float(price[j]),target_net=float(target[0,j]),qty_before=float(qb[0,j]),qty_after=float(q[0,j]),requested_gross=raw,rejection='BELOW_PRECISION_OR_NO_CASH'))
  mark(x[d*24+1:d*24+24,0,:])
  if k==D-1:
   xp=x[d*24+23,0,:];qb=q.copy()
   cash,q,tr=rebalance(cash,q,xp,np.zeros_like(q),fee,slip,minimum,discrete)
   executed+=(tr['sold']>0).sum(axis=1);skipped+=((qb>0)&(tr['sold']==0)).sum(axis=1)
   fees[:,k]+=tr['fees'];slips[:,k]+=tr['slippage'];turns[:,k]+=tr['turn']
   if log:
    for j,s in enumerate(SYMS):
     base=dict(day=int(t[d*24]),time=int(t[d*24]+23*60),symbol=s,side='SELL',reason='TERMINAL_TECHNICAL_LIQUIDATION',reference_price=float(xp[j]),target_net=0.,qty_before=float(qb[0,j]),qty_after=float(q[0,j]))
     gross=float(tr['sold'][0,j])
     if gross>0:events.append(dict(**base,gross_qty=gross,cash_delta=gross*xp[j]*(1-slip)*(1-fee),fee_quote=gross*xp[j]*(1-slip)*fee,fee_base=0.,slippage_quote=gross*xp[j]*slip))
     elif qb[0,j]>0:rejections.append(dict(**base,requested_gross=float(qb[0,j]),rejection='TERMINAL_DUST_NOT_LIQUIDATABLE'))
   endprice=xp
   finalnav=cash+(q*endprice).sum(axis=1);peak=np.maximum(peak,finalnav);mdd=np.minimum(mdd,finalnav/peak-1)
  else:endprice=x[d*24+23,3,:]
  nav[:,k]=cash+(q*endprice).sum(axis=1);qs[:,k]=q;cs[:,k]=cash
  lastw=w.copy()
 events.sort(key=lambda e:(e['time'],e['side']=='BUY',e['symbol']))
 dates=np.array([str(np.datetime64('1970-01-01')+np.timedelta64(int(t[d*24]),'m'))[:10] for d in dayids])
 return dict(nav=nav,q=qs,cash=cs,fees=fees,slips=slips,turn=turns,mdd=mdd,exposure=exp_sum/hours,cash_fraction=cash_sum/hours,executed=executed,skipped=skipped,events=events,rejections=rejections,dates=dates,days=t[dayids*24],initial=capital)
def metrics(r):
 v=r['nav'][0];initial=r['initial'];ret=v/np.r_[initial,v[:-1]]-1
 annual={y:float(v[np.flatnonzero(np.char.startswith(r['dates'],str(y)))[-1]]/(initial if y==2024 else v[np.flatnonzero(np.char.startswith(r['dates'],'2024'))[-1]])-1) for y in [2024,2025] if np.any(np.char.startswith(r['dates'],str(y)))}
 dd=float(r['mdd'][0]);ann=(v[-1]/initial)**(365/len(v))-1
 return dict(final_mtm=float(v[-1]),liquidatable_cash=float(r['cash'][0,-1]),dust_mtm=float(v[-1]-r['cash'][0,-1]),net_return=float(v[-1]/initial-1),net_2024=annual.get(2024,0.),net_2025=annual.get(2025,0.),mdd=dd,fees=float(r['fees'].sum()),slippage=float(r['slips'].sum()),turnover=float(r['turn'].sum()),executed=int(r['executed'][0]),skipped=int(r['skipped'][0]),avg_exposure=float(r['exposure'][0]),cash_fraction=float(r['cash_fraction'][0]),sharpe=float(ret.mean()/ret.std(ddof=1)*np.sqrt(365)) if ret.std(ddof=1)>0 else 0.,calmar=float(ann/abs(dd)) if dd<0 else 0.,best_day=str(r['dates'][ret.argmax()]),best_day_return=float(ret.max()),worst_day=str(r['dates'][ret.argmin()]),worst_day_return=float(ret.min()))
def combine_sleeves(rs,capital):
 r={k:rs[0][k] for k in ['dates','days']}
 for k in ['nav','cash','q','fees','slips','turn']:r[k]=sum(z[k] for z in rs)
 # Conservatively compute exact aggregate hourly risk separately in run.py.
 for k in ['executed','skipped']:r[k]=sum(z[k] for z in rs)
 r['mdd']=np.array([0.]);r['exposure']=sum(z['exposure'] for z in rs)/3;r['cash_fraction']=sum(z['cash_fraction'] for z in rs)/3
 r['events']=sorted(sum([z['events'] for z in rs],[]),key=lambda e:(e['time'],e['side']=='BUY',e['symbol']));r['rejections']=sum([z['rejections'] for z in rs],[]);r['initial']=capital
 return r
