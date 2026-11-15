"""Build a deterministic allowlisted research runtime ZIP; no secrets, datasets or deployment."""
from pathlib import Path
import json,hashlib,zipfile
R=Path(__file__).resolve().parent
PATHS=[
 'r2_validation/observer.py','r2_validation/public_data.py','r2_validation/core.py',
 'r2_validation/execution.py','r2_validation/PREREGISTRATION.json',
 'r2_validation/DEPLOYMENT_STATUS.json','r2_validation/runtime_requirements.txt',
 't01_profit_rank/profit_rank.py','t01_profit_rank/PREREGISTRATION.json']
def main():
 base=R.parent;out=R/'deployment';out.mkdir(exist_ok=True)
 data={name:(base/name).read_bytes() for name in PATHS}
 manifest=dict(kind='RESEARCH_RUNTIME_ONLY_NOT_DEPLOYMENT',files={n:hashlib.sha256(b).hexdigest() for n,b in data.items()})
 data['manifest.json']=(json.dumps(manifest,indent=2,sort_keys=True)+'\n').encode()
 archive=out/'R2_OBSERVER_RUNTIME.zip'
 with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_STORED) as z:
  for n,b in sorted(data.items()):
   info=zipfile.ZipInfo(n,date_time=(2026,11,14,0,0,0));info.compress_type=zipfile.ZIP_STORED;info.external_attr=0o100600<<16
   z.writestr(info,b)
 with zipfile.ZipFile(archive) as z:
  assert z.testzip() is None and sorted(z.namelist())==sorted(data)
  assert all(hashlib.sha256(z.read(n)).hexdigest()==h for n,h in manifest['files'].items())
 result=dict(archive='deployment/'+archive.name,bytes=archive.stat().st_size,sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),source_files=len(PATHS),entries=len(data),deployment_status=json.loads(data['r2_validation/DEPLOYMENT_STATUS.json'])['status'],manifest=manifest)
 (R/'DEPLOYMENT_BUNDLE_MANIFEST.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
 print(json.dumps({k:v for k,v in result.items() if k!='manifest'}))
if __name__=='__main__':main()
