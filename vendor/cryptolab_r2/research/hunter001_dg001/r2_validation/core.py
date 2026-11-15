"""Shared research-only strategy. Frozen parent implementation is read-only."""
from pathlib import Path
import importlib.util,math,json
import numpy as np
ROOT=Path(__file__).resolve().parent
P=json.loads((ROOT/'PREREGISTRATION.json').read_text(encoding='utf-8'))
spec=importlib.util.spec_from_file_location('frozen_t01_profit_rank',ROOT.parent/'t01_profit_rank/profit_rank.py')
frozen=importlib.util.module_from_spec(spec);spec.loader.exec_module(frozen)
SYMS=frozen.SYMS
rebalance=frozen.rebalance
factors=frozen.factors
targets=frozen.targets
COSTS={k:(v['fee'],v['adverse_slippage']) for k,v in P['candidate']['costs'].items()}
def signal(cl):
 cl=np.asarray(cl,float);assert cl.shape==(29,3) and np.isfinite(cl).all() and (cl>0).all()
 e=cl[-1]>cl[0];mom=np.log(cl[-1]/cl[0]);vol=np.log(cl[1:]/cl[:-1]).std(axis=0,ddof=1)*np.sqrt(28)
 score=np.divide(mom,vol,out=np.full(3,np.nan),where=vol>0)
 return dict(eligible=e,momentum=mom,score=score,BASELINE=e.astype(float)/3,R2=targets(e[None],{'R2':score[None]},'R2')[0])
def persistent_random(eligible,r2,draws=1000,seed=6111501):
 rng=np.random.default_rng(seed);w=np.repeat((eligible/3)[None],draws,axis=0);last=None;prev_r2=None
 for d in range(len(eligible)):
  if eligible[d].sum()!=3:last=None;prev_r2=None;continue
  omit=int(np.argmin(r2[d]))
  if last is None:last=rng.integers(3,size=draws)
  elif omit!=prev_r2:last=(last+rng.integers(1,3,size=draws))%3
  w[:,d]=.5;w[np.arange(draws),d,last]=0;prev_r2=omit
 return w
