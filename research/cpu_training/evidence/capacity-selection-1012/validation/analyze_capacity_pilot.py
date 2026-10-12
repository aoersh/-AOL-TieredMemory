#!/usr/bin/env python3
"""独立重算容量租约和三个进程均值；不把步骤当作独立重复。"""
import argparse,hashlib,json,statistics as st
from pathlib import Path
from run_capacity_matrix import CONDITIONS,SELECTION_CONDITIONS
from analyze_closure import estimate,native

def read(p):return json.loads(p.read_text())
def audit(root):
 root=Path(root);protocol=read(root/'protocol.json');diag=protocol['diagnostic'];n=1 if diag else 3
 subset=protocol.get('selection_matrix',False);conditions=SELECTION_CONDITIONS if subset else CONDITIONS
 assert read(root/'completed.json')['runs']==n*len(conditions)
 progress=read(root/'progress.json');commands=read(root/'commands.json')
 assert len(progress)==len(commands)==n*len(conditions)
 assert [x['name'] for x in progress]==[x[0] for x in commands]
 assert all(x['start_ns']<x['end_ns'] for x in progress)
 assert all(x['end_ns']<=y['start_ns'] for x,y in zip(progress,progress[1:]))
 hashes=None;loss=None;selection=None;data={};identities={};environment=None
 for condition,(policy,budget) in conditions.items():
  data[condition]={}
  for rep in range(n):
   d=root/f'{condition}-r{rep}';m=read(d/'manifest.json');rows=read(d/'steps.json');caps=read(d/'capacity.json')
   assert m['policy']==policy and m['capacity_bytes']==budget*1048576 and m['diagnostic']==diag
   assert m['compute_cpus']==list(range(8)) and m['migration_cpu']==8
   selected=[10 if condition.endswith('10') else 34] if subset and condition!='direct' else None
   assert m.get('selected_ids')==selected
   system=(m['kernel'],m['sysctl'])
   if environment is None:environment=system
   assert environment==system
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
    assert len(initial)==len(c['decisions'])
    assert all(x['initial_node'] in [0,2] and x['bytes']>0 and x['bytes']%4096==0 for x in initial.values())
    assert sum(x['bytes'] for x in initial.values())==r['fresh_buffer_bytes']
    if subset and selected:
     assert initial[selected[0]]['bytes']==33554432 and set(reserved)==set(selected)
    expected={k:x['bytes'] for k,x in initial.items() if x['initial_node']==0}
    if policy=='pack':assert reserved==expected and r['moved_bytes']==0
    else:
     assert not expected
     assert sum(reserved.values())==r['moved_bytes']
     assert r['moved_bytes']==sum(x['bytes'] for x in r['task_timeline'])
     for task in r['task_timeline']:
      native(task)
      assert task['destination']==0 and task['bytes']==initial[task['id']]['bytes']
      assert r['backward_start_ns']<=task['start_ns']<=task['done_ns']<=r['backward_end_ns']
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
 return dict(diagnostic=diag,selection_matrix=subset,source_hashes=hashes,loss=loss,selection=selection,process_means=data,
  means={c:{k:st.mean(v) for k,v in x.items()} for c,x in data.items()},admitted_ids=identities)
def main():
 p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('--diagnostic-reference',type=Path);p.add_argument('--output',type=Path);a=p.parse_args()
 s=audit(a.root)
 if not s['diagnostic']:
  ref=audit(a.diagnostic_reference);assert ref['diagnostic'] and ref['source_hashes']==s['source_hashes']
  assert ref['loss']==s['loss'][:3] and ref['selection']==s['selection'][:3]
  d=s['process_means']
  pairs=[('pack10','direct'),('pack34','direct'),('demand10','direct'),('demand34','direct'),('pack10','pack34'),('demand10','demand34')] if s['selection_matrix'] else [('pack32','direct'),('pack64','direct'),('demand32','direct'),('demand64','direct'),('demand32','pack32'),('demand64','pack64')]
  s['contrasts']={left+'-'+right:{k:estimate([x-y for x,y in zip(d[left][k],d[right][k])]) for k in d[left]} for left,right in pairs}
 out=a.output or a.root/'verified-analysis';out.mkdir(exist_ok=False)
 (out/'summary.json').write_text(json.dumps(s,indent=2)+'\n');(out/'analyzer.py').write_bytes(Path(__file__).read_bytes())
 (out/'completed.json').write_text(json.dumps(dict(audit_passed=True,runs=len(s['process_means'])*(1 if s['diagnostic'] else 3)))+'\n')
 print(json.dumps(s['means'],indent=2))
if __name__=='__main__':main()
