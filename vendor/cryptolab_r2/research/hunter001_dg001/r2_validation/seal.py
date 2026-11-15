from pathlib import Path
import hashlib,sys
R=Path(__file__).resolve().parent
manifest=R/'SHA256SUMS.txt'
def files():
 return [p for p in sorted(R.rglob('*')) if p.is_file() and p!=manifest and '__pycache__' not in p.parts and '.pytest_cache' not in p.parts and not(p.suffix=='.gz' and p.parent in [R/'history/raw',R/'evidence/control_raw'])]
if '--verify' in sys.argv:
 n=0
 for line in manifest.read_text(encoding='utf-8').splitlines():
  h,p=line.split('  ',1);assert hashlib.sha256((R/p).read_bytes()).hexdigest()==h,p;n+=1
 print('SHA256 PASS',n)
else:
 manifest.write_bytes((''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.relative_to(R).as_posix()+'\n' for p in files())).encode())
 print('SEALED',len(files()))
