"""Allowlisted public GET only; no credentials, account API or trading imports."""
from pathlib import Path
from collections import deque
from urllib.request import Request,urlopen
from urllib.parse import urlencode
import time,json,hashlib,gzip,datetime,uuid,threading
UTC=datetime.timezone.utc
ROOT=Path(__file__).resolve().parent
SYMS=['BTCUSDT','ETHUSDT','SOLUSDT']
ALLOWED={'/v5/market/kline','/v5/market/instruments-info','/v5/market/orderbook','/v5/market/time'}
def utc():return datetime.datetime.now(UTC)
def ms(s):return int(datetime.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()*1000)
def iso(ts):return datetime.datetime.fromtimestamp(ts/1000,UTC).isoformat()
def guard_kline(start,end):
 assert start<=end
 # Original Jan-Sep2026 holdout remains closed, even for availability probes.
 assert not(start<ms('2026-10-01T00:00:00Z') and end>=ms('2026-01-01T00:00:00Z')),'CLOSED_OOS'
class Public:
 def __init__(self,folder):self.folder=Path(folder);self.folder.mkdir(parents=True,exist_ok=True);self.receipts=deque(maxlen=100);self.lock=threading.Lock()
 def get(self,path,**params):
  assert path in ALLOWED
  if path=='/v5/market/kline':
   assert params.get('category')=='spot';guard_kline(int(params['start']),int(params['end']))
  if path!='/v5/market/time':assert params.get('category')=='spot' and params.get('symbol') in SYMS
  url='https://api.bybit.com'+path+'?'+urlencode(params);started=utc();mono=time.monotonic()
  try:
   with urlopen(Request(url,headers={'User-Agent':'CryptoLab-R2-public-research/1.0','Cache-Control':'no-cache'}),timeout=12) as r:
    b=r.read(1_000_001);status=r.status
   assert len(b)<=1_000_000,'RESPONSE_CAP'
   finished=utc();o=json.loads(b)
   receipt=dict(id=uuid.uuid4().hex,url=url,started_at=started.isoformat(),available_at=finished.isoformat(),ingested_at=finished.isoformat(),available_kind='OBSERVED_HTTP_RECEIPT_NOT_HISTORICAL_PUBLISH_TIME',rtt_ms=(time.monotonic()-mono)*1000,server_at_ms=o.get('time'),http_status=status,retCode=o.get('retCode'),bytes=len(b),sha256=hashlib.sha256(b).hexdigest())
   file=receipt['id']+'.json.gz';(self.folder/file).write_bytes(gzip.compress(b,mtime=0));receipt['file']=file
   self.receipts.append(receipt)
   with self.lock:
    with (self.folder/'RECEIPTS.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(receipt)+'\n')
   if o.get('retCode')!=0:raise ValueError('PUBLIC_API_ERROR '+str(o.get('retCode'))+' '+str(o.get('retMsg')))
   return o,receipt
  except Exception as e:
   with (self.folder/'ERRORS.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(dict(at=utc().isoformat(),url=url,error=str(e)))+'\n')
   raise
def validate_ohlc(rows,step,start,end):
 by={}
 for row in rows:
  a=int(row[0]);v=list(map(float,row[1:5]))
  assert start<=a<end and a%step==0 and a not in by,'RANGE_DUPLICATE_ALIGNMENT'
  assert all(__import__('math').isfinite(p) and p>0 for p in v)
  assert v[1]>=max(v[0],v[3]) and v[2]<=min(v[0],v[3]),'INVALID_OHLC'
  by[a]=v
 expected=list(range(start,end,step))
 assert sorted(by)==expected,('GAP_OR_MISSING',len(by),len(expected),expected[0] if expected else None)
 return expected,[by[a] for a in expected]
