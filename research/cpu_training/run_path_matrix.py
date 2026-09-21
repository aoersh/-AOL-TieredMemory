#!/usr/bin/env python3
"""Serial two-shape path pilot with paired randomized independent processes."""
import argparse
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('output',type=Path)
p.add_argument('--phase',choices=['diagnostic','timing'],required=True)
p.add_argument('--repeats',type=int,default=5)
a=p.parse_args()
a.output.mkdir(parents=True,exist_ok=False)
rng=random.Random(20260921)
commands=[]
for rep in range(1 if a.phase=='diagnostic' else a.repeats):
 for shape,batch,seq in [('small',4,128),('large',8,512)]:
  modes=['dram','direct','sync','async']
  rng.shuffle(modes)
  for mode in modes:
   name=f'{shape}-r{rep}-{mode}'
   cmd=['numactl','--physcpubind=0-8','--membind=0',sys.executable,
        'research/cpu_training/run_access_paths.py',str(a.output/name),
        '--mode',mode,'--batch',str(batch),'--sequence',str(seq),
        '--steps','3' if a.phase=='diagnostic' else '20',
        '--warmup','0' if a.phase=='diagnostic' else '5']
   if a.phase=='diagnostic': cmd+=['--diagnostic']
   commands.append((name,cmd))
(a.output/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
(a.output/'run_path_matrix.py').write_bytes(Path(__file__).read_bytes())
for name,cmd in commands:
 print('RUN',name,flush=True)
 with (a.output/f'{name}.log').open('w') as log:
  result=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT)
 if result.returncode:
  print((a.output/f'{name}.log').read_text(),flush=True)
  raise SystemExit(result.returncode)
 print('PASS',name,flush=True)
# Cross-policy candidate identities must match; audit actual source snapshots.
for shape in ('small','large'):
 for rep in range(1 if a.phase=='diagnostic' else a.repeats):
  signatures=[]
  for mode in ('dram','direct','sync','async'):
   path=a.output/f'{shape}-r{rep}-{mode}'
   m=json.loads((path/'manifest.json').read_text())
   for name,digest in m['source_hashes'].items():
    assert hashlib.sha256((path/name).read_bytes()).hexdigest()==digest
   rows=json.loads((path/'steps.json').read_text())
   signatures.append([r['selection_sha256'] for r in rows])
  assert all(s==signatures[0] for s in signatures), (shape,rep,'candidate set mismatch')
(a.output/'completed.json').write_text(json.dumps(dict(runs=len(commands),candidate_sets_match=True))+'\n')
