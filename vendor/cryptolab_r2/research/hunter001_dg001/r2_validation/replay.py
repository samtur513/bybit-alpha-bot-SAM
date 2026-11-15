"""Frozen parent replay; only explicit period boundaries are generalized."""
from core import *
P=frozen.P
def replay(t,x,weights,capital,cost,minimum,start,end,log=True):
 """Weights [accounts,days,3] or [days,3]; daily snapshots + hourly risk."""
 if weights.ndim==2:weights=weights[None,:,:]
 n=len(weights);fee,slip=P['costs'][cost];discrete=minimum>0
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