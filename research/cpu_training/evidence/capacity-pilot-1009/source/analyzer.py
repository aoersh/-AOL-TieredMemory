#!/usr/bin/env python3
"""独立重算容量租约和三个进程均值；不把步骤当作独立重复。"""
import argparse,hashlib,json,statistics as st
from pathlib import Path
from run_capacity_matrix import CONDITIONS
from analyze_closure import estimate

def read(p):return json.loads(p.read_text())
def audit(root):
 root=Path(root);diag=read(root/'protocol.json')['diagnostic'];n=1 if diag else 3
 assert read(root/'completed.json')['runs']==n*len(CONDITIONS)
 assert len(read(root/'progress.json'))==n*len(CONDITIONS)
 hashes=None;loss=None;selection=None;data={};identities={}
 for condition,(policy,budget) in CONDITIONS.items():
  data[condition]={}
  for rep in range(n):
   d=root/f'{condition}-r{rep}';m=read(d/'manifest.json');rows=read(d/'steps.json');caps=read(d/'capacity.json')
   assert m['policy']==policy and m['capacity_bytes']==budget*1048576 and m['diagnostic']==diag
   assert m['compute_cpus']==list(range(8)) and m['migration_cpu']==8
   assert read(d/'completed.json')['all_leases_released']
   if hashes is None:hashes=m['source_hashes']
   assert hashes==m['source_hashes']
   for f,h in hashes.items():assert hashlib.sha256((d/f).read_bytes()).hexdigest()==h
   assert len(rows)==len(caps)==(3 if diag else 105)
   current=[r['loss'] for r in rows];chosen=[r['selection_sha256'] for r in rows]
   if loss is None:loss=current;selection=chosen
   assert loss==current and selection==chosen
   metrics=[]
   for i,(r,c) in enumerate(zip(rows,caps)):
    assert r['step']==i and r['measured']==(diag or i>=5)
    assert r['step_ns']==r['end_ns']-r['start_ns']
    assert r['fresh_buffer_bytes']==140673024 and r['major_faults']==0
    live=peak=area=0;leases={};last=r['start_ns'];reserved={};released=set()
    for event in c['ledger']:
     t=event['time_ns'];assert last<=t<=r['end_ns']
     area+=live*(t-last);last=t
     key=event['id'];size=event['bytes']
     if event['event']=='reserve':
      assert key not in leases and key not in reserved
      leases[key]=size;reserved[key]=size;live+=size
     else:
      assert event['event']=='release' and leases.pop(key)==size
      released.add(key);live-=size
     assert live==event['live_bytes'] and 0<=live<=budget*1048576
     peak=max(peak,live)
    assert live==0 and not leases and set(reserved)==released
    assert peak==c['peak_bytes'] and c['capacity_bytes']==budget*1048576
    initial={x['id']:x for x in c['decisions']}
    assert sum(x['bytes'] for x in initial.values())==r['fresh_buffer_bytes']
    expected={k:x['bytes'] for k,x in initial.items() if x['initial_node']==0}
    if policy=='pack':assert reserved==expected and r['moved_bytes']==0
    else:
     assert not expected
     assert sum(reserved.values())==r['moved_bytes']
     assert r['moved_bytes']==sum(x['bytes'] for x in r['task_timeline'])
    if policy=='direct':assert not reserved
    identities.setdefault(condition,[]).append(sorted(reserved))
    if r['measured']:
     metrics.append(dict(step_ms=r['step_ns']/1e6,forward_ms=r['forward_ns']/1e6,backward_ms=r['backward_ns']/1e6,
      migration_mib=r['moved_bytes']/1048576,peak_mib=peak/1048576,dram_mib_ms=area/1048576/1e6))
   if diag:
    assert read(d/'correctness.json')=={'max_abs_error':0,'pass_':True}
    for block,c in zip(read(d/'events.json'),caps):
     initial={x['id']:x for x in c['decisions']};promoted={e['id'] for e in block['events'] if e['event']=='migration'}
     for e in block['events']:
      if e['event'] in ['initial','unpack']:
       x=initial[e['id']];node=0 if e['event']=='unpack' and e['id'] in promoted else x['initial_node']
       assert e['nodes']=={str(node):x['bytes']//4096}
   timing=read(d/'run-timing.json');assert timing['summed_step_ns']==sum(r['step_ns'] for r in rows)
   assert timing['total_run_ns']>=timing['setup_ns']+timing['teardown_ns']+timing['summed_step_ns']
   for k in metrics[0]:data[condition].setdefault(k,[]).append(st.mean(x[k] for x in metrics))
   data[condition].setdefault('amortized_run_ms',[]).append(timing['total_run_ns']/len(rows)/1e6)
 return dict(diagnostic=diag,source_hashes=hashes,loss=loss,selection=selection,process_means=data,
  means={c:{k:st.mean(v) for k,v in x.items()} for c,x in data.items()},admitted_ids=identities)
def main():
 p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('--diagnostic-reference',type=Path);a=p.parse_args()
 s=audit(a.root)
 if not s['diagnostic']:
  ref=audit(a.diagnostic_reference);assert ref['diagnostic'] and ref['source_hashes']==s['source_hashes']
  assert ref['loss']==s['loss'][:3] and ref['selection']==s['selection'][:3]
  d=s['process_means'];s['contrasts']={left+'-'+right:{k:estimate([x-y for x,y in zip(d[left][k],d[right][k])]) for k in d[left]} for left,right in [('pack32','direct'),('pack64','direct'),('demand32','direct'),('demand64','direct'),('demand32','pack32'),('demand64','pack64')]}
 out=a.root/'verified-analysis';out.mkdir(exist_ok=False)
 (out/'summary.json').write_text(json.dumps(s,indent=2)+'\n');(out/'analyzer.py').write_bytes(Path(__file__).read_bytes())
 print(json.dumps(s['means'],indent=2))
if __name__=='__main__':main()
