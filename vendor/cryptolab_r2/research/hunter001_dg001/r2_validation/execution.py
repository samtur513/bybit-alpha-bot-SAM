"""Public-snapshot cost estimates. No order submission, signing or account API."""
from core import *
from decimal import Decimal,ROUND_DOWN
def floorq(a,step):
 assert math.isfinite(a) and a>=0 and math.isfinite(step) and step>0
 return float((Decimal(str(a))/Decimal(str(step))).to_integral_value(rounding=ROUND_DOWN)*Decimal(str(step)))
def validate_market(meta,book,received_ms):
 assert meta['symbol']==book['s'] and meta['symbol'] in SYMS and meta['status']=='Trading'
 lot=meta['lotSizeFilter'];out={}
 for key in ['basePrecision','quotePrecision','minOrderAmt','maxMarketOrderQty']:
  value=float(lot[key]);assert math.isfinite(value) and value>0,(key,'UNKNOWN_OR_INVALID_SPEC');out[key]=value
 bids=[(float(p),float(q)) for p,q in book['b']];asks=[(float(p),float(q)) for p,q in book['a']]
 assert bids and asks and all(math.isfinite(p) and math.isfinite(q) and p>0 and q>0 for p,q in bids+asks)
 assert all(bids[k][0]>bids[k+1][0] for k in range(len(bids)-1))
 assert all(asks[k][0]<asks[k+1][0] for k in range(len(asks)-1))
 assert bids[0][0]<asks[0][0],'CROSSED_OR_LOCKED_BOOK'
 assert 0<=received_ms-int(book['ts'])<=5000,'STALE_OR_FUTURE_BOOK'
 return dict(meta=meta,book=book,bids=bids,asks=asks,mid=(bids[0][0]+asks[0][0])/2,limits=out)
def sweep(levels,quantity):
 remain=quantity;value=0.
 for price,size in levels:
  take=min(remain,size);value+=take*price;remain-=take
  if remain<=1e-12:return value,True
 return value,False
def quote_quantity(levels,budget):
 q=0.
 for price,size in levels:
  take=min(size,budget/price);q+=take;budget-=take*price
  if budget<=1e-10:break
 return q
def trade_account(cash,qty,weights,markets,cost,policy):
 """Both BASELINE/R2 call this same engine with identical policy/limits."""
 assert math.isfinite(cash) and cash>=0 and len(qty)==3 and all(math.isfinite(q) and q>=0 for q in qty)
 assert len(weights)==3 and all(math.isfinite(w) and w>=0 for w in weights) and sum(weights)<=1+1e-12
 fee,slip=COSTS[cost];price=np.array([m['mid'] for m in markets]);q=np.array(qty,float);before=cash+q@price;target=before*np.array(weights)/(price*(1+fee+slip));events=[]
 if policy=='MODEL_C5':
  cb=float(cash);qb=q.copy()
  cc,qq,tr=rebalance(np.array([float(cash)]),q[None],price,target[None],fee,slip,5,True)
  for side,key in [('SELL','sold'),('BUY','bought')]:
   for j,s in enumerate(SYMS):
    gross=float(tr[key][0,j]);requested=max(0,float(qb[j]-target[j])) if side=='SELL' else max(0,float((target[j]-qb[j])/(1-fee)))
    if requested<=1e-14:continue
    delta=gross*price[j]*(1-slip)*(1-fee) if side=='SELL' else -gross*price[j]*(1+slip)
    fq=gross*price[j]*fee*(1-slip if side=='SELL' else 1)
    status='ESTIMATED_NOT_OBSERVED_FILL' if gross>0 else 'REJECT_MINIMUM_PRECISION_OR_BALANCE'
    qa=qb[j]-gross if side=='SELL' else qb[j]+gross*(1-fee)
    events.append(dict(symbol=s,side=side,status=status,gross_qty=gross,cash_delta=delta,fee_quote=fq,fee_base=gross*fee if side=='BUY' else 0.,buffer_cost=gross*price[j]*slip,book_spread_impact=0.,reference_mid=float(price[j]),execution_price=float(price[j]*(1-slip if side=='SELL' else 1+slip)),cash_before=cb,cash_after=cb+delta,qty_before=float(qb[j]),qty_after=float(qa)))
    cb+=delta;qb[j]=qa
  assert abs(cb-cc[0])<1e-6
  return float(cc[0]),qq[0].tolist(),events
 assert policy=='OBSERVED_BOOK'
 def event(j,side,requested,gross,status,quote=0):
  nonlocal cash,q
  cb=float(cash);qb=float(q[j]);mid=price[j];fq=gross*fee*mid if side=='BUY' else quote*fee
  delta=-quote if side=='BUY' else quote*(1-fee)
  cash+=delta;q[j]+=gross*(1-fee) if side=='BUY' else -gross
  assert cash>=-1e-8 and q[j]>=-1e-10
  factor=1+slip if side=='BUY' else 1-slip
  base_quote=quote/factor if gross else 0.
  impact=(base_quote-gross*mid) if side=='BUY' else (gross*mid-base_quote)
  buffer=abs(quote-base_quote)
  events.append(dict(symbol=SYMS[j],side=side,status=status,gross_qty=gross,cash_delta=delta,fee_quote=fq,fee_base=gross*fee if side=='BUY' else 0.,buffer_cost=buffer,book_spread_impact=impact,reference_mid=float(mid),execution_price=float(quote/gross) if gross else 0.,cash_before=cb,cash_after=float(cash),qty_before=qb,qty_after=float(q[j])))
 for j,m in enumerate(markets):
  req=max(0,float(q[j]-target[j]))
  if req<=1e-14:continue
  limits=m['limits'];gross=floorq(req,limits['basePrecision']);quote,depth=sweep(m['bids'],gross);quote*=1-slip
  reason='ESTIMATED_NOT_OBSERVED_FILL'
  if gross==0 or quote<limits['minOrderAmt']:reason='REJECT_MINIMUM_OR_PRECISION'
  elif gross>limits['maxMarketOrderQty']:reason='REJECT_MAX_QUANTITY'
  elif not depth:reason='REJECT_INSUFFICIENT_DEPTH'
  if reason.startswith('REJECT'):event(j,'SELL',req,0.,reason)
  else:event(j,'SELL',req,gross,reason,quote)
 wants=np.maximum(target-q,0)/(1-fee);needed=[];valid=[]
 for j,m in enumerate(markets):
  value,depth=sweep(m['asks'],float(wants[j]));needed.append(value*(1+slip));valid.append(depth)
 total=sum(v for v,d in zip(needed,valid) if d);scale=min(1,cash/total) if total>0 else 1
 for j,m in enumerate(markets):
  if wants[j]<=1e-14:continue
  limits=m['limits']
  if not valid[j]:event(j,'BUY',float(wants[j]),0.,'REJECT_INSUFFICIENT_DEPTH');continue
  budget=floorq(max(0,needed[j]*scale),limits['quotePrecision'])
  gross=floorq(quote_quantity(m['asks'],budget/(1+slip)),limits['basePrecision']);value,depth=sweep(m['asks'],gross);value*=1+slip
  reason='ESTIMATED_NOT_OBSERVED_FILL'
  if gross==0 or value<limits['minOrderAmt']:reason='REJECT_MINIMUM_OR_PRECISION'
  elif gross>limits['maxMarketOrderQty']:reason='REJECT_MAX_QUANTITY'
  elif not depth:reason='REJECT_INSUFFICIENT_DEPTH'
  elif value>cash+1e-8:reason='REJECT_BALANCE'
  if reason.startswith('REJECT'):event(j,'BUY',float(wants[j]),0.,reason)
  else:event(j,'BUY',float(wants[j]),gross,reason,value)
 return max(0.,float(cash)),np.maximum(q,0).tolist(),events
def valuation(cash,q,markets,cost,policy):
 mid=sum(q[j]*markets[j]['mid'] for j in range(3))+cash
 liquid,remain,ev=trade_account(cash,q,[0.,0.,0.],markets,cost,policy)
 unliquidated_mtm=sum(remain[j]*markets[j]['mid'] for j in range(3))
 dust=sum(remain[j]*markets[j]['mid'] for j in range(3) if remain[j]*markets[j]['bids'][0][0]<(5 if policy=='MODEL_C5' else markets[j]['limits']['minOrderAmt']))
 return dict(mtm=mid,liquidatable_cash=liquid,dust_mtm=dust,unliquidated_mtm=unliquidated_mtm,unliquidated=remain)
