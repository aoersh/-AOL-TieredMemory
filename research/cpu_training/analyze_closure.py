#!/usr/bin/env python3
"""严格审计两个收敛矩阵，按三次进程均值配对；不覆写输出。"""
import argparse,hashlib,json,math,statistics as st
from pathlib import Path
from run_closure_matrix import CONDITIONS
def read(p):return json.loads(p.read_text())
def estimate(v):
 assert len(v)==3
 m=st.mean(v);h=4.30265273*st.stdev(v)/math.sqrt(3)
 return dict(mean=m,ci95=[m-h,m+h],values=v)
def native(e):
 assert e['native_wall_ns']==e['native_end_ns']-e['native_start_ns']
 assert e['syscall_ns']==e['native_pre_ns']+e['native_wall_ns']+e['native_post_ns']
 assert e['syscall_start_ns']<=e['native_start_ns']<=e['native_end_ns']<=e['syscall_end_ns']
 assert e['native_cpu_ns']>=0
def audit(root):
 root=Path(root);p=read(root/'protocol.json');done=read(root/'completed.json')
 kind=p['kind'];diag=p['diagnostic']
 assert p['conditions']==CONDITIONS[kind] and done['kind']==kind and done['diagnostic']==diag
 assert p['repeats']==(1 if diag else 3)
 assert p['steps']==(3 if diag else 100) and p['warmup']==(0 if diag else 5)
 commands=read(root/'commands.json');progress=read(root/'progress.json')
 assert done['runs']==len(commands)==len(progress)==p['repeats']*len(p['conditions'])
 assert [x[0] for x in commands]==[x['name'] for x in progress]
 hashes=None;env=None;loss=None;selection=None;process={};checks=[]
 for c in p['conditions']:
  process[c]={}
  for rep in range(p['repeats']):
   name=f'{c}-r{rep}';d=root/name;m=read(d/'manifest.json');rows=read(d/'steps.json')
   timing=read(d/'run-timing.json')
   if hashes is None:hashes=m['source_hashes']
   assert hashes==m['source_hashes']
   for f,h in hashes.items():assert hashlib.sha256((d/f).read_bytes()).hexdigest()==h
   configuration=m.get('configuration',m)
   assert configuration['diagnostic']==diag
   assert configuration['steps']==p['steps'] and configuration['warmup']==p['warmup']
   system=(m['kernel'],m.get('sysctl', {k:m.get(k) for k in ['numa_balancing','numa_balancing_pte_scale','perf_event_paranoid']}))
   if env is None:env=system
   assert env==system
   assert len(rows)==p['steps']+p['warmup']
   assert [r['step'] for r in rows]==list(range(len(rows)))
   assert timing['summed_step_ns']==sum(r['step_ns'] for r in rows)
   assert timing['total_run_ns']>=timing['setup_ns']+timing['teardown_ns']+timing['summed_step_ns']
   values=[]
   active=c in ['fresh_migrate','reuse_migrate','prefetch']
   if kind=='pressure':
    assert m['protocol_version']==4 and m['migrate']==active
    assert m['mode']==('fresh' if c.startswith('fresh') else 'reuse')
    assert m['target_bytes']==m['pressure_bytes']==33554432
    assert m['cpus']==list(range(8)) and m['worker_cpu']==8
    assert timing['mappings_closed'] and timing['final_correctness']
   else:
    cfg=configuration
    assert cfg['mode']==('ranked' if active else 'direct')
    assert cfg['initial_target_node']==(0 if c=='dram' else 2)
    assert cfg['target_ids']==[10] and cfg['buffer_policy']=='reuse_dram'
    assert cfg.get('measure_target_pack',False)==p.get('measure_target_pack',False)
    assert cfg['migration_action']==('real_vectorized_native' if active else None)
    assert (cfg['batch'],cfg['sequence'],cfg['width'],cfg['heads'],cfg['layers'])==(8,512,128,4,2)
    assert timing['pool_bytes']==107118592 and timing['pool_closed']
    current_loss=[r['loss'] for r in rows];current_selection=[r['selection_sha256'] for r in rows]
    if loss is None:loss=current_loss;selection=current_selection
    assert loss==current_loss and selection==current_selection
   for r in rows:
    assert r['measured']==(r['step']>=p['warmup'])
    assert r['step_ns']==r['end_ns']-r['start_ns'] and r['step_ns']>0
    assert r['minor_faults']>=0 and r['major_faults']==0
    v=dict(step_ms=r['step_ns']/1e6,minor_faults=r['minor_faults'])
    if kind=='pressure':
     assert r['write_bytes']==33554432 and r['forward_bytes']==r['reset_bytes']==(33554432 if active else 0)
     assert r['pressure_ns']==r['pressure_end_ns']-r['pressure_start_ns']
     assert r['start_ns']<=r['pressure_start_ns']<=r['pressure_end_ns']<=r['joined_ns']<=r['reset_start_ns']<=r['reset_end_ns']<=r['end_ns']
     w=r['worker'];assert w['affinity']==[8] and w['tid']>0
     assert r['start_ns']<=w['start_ns']<=w['end_ns']<=r['joined_ns']
     assert r['concurrent_ns']==r['joined_ns']-min(r['pressure_start_ns'],w['start_ns'])
     assert r['reset_ns']==r['reset_end_ns']-r['reset_start_ns']
     if active:
      e=w['native'];native(e);native(r['reset'])
      assert e['native_tid']==r['reset']['native_tid']==w['tid']
      assert w['start_ns']<=e['native_start_ns']<=e['native_end_ns']<=w['end_ns']
      assert r['reset_start_ns']<=r['reset']['native_start_ns']<=r['reset']['native_end_ns']<=r['reset_end_ns']
      overlap=max(0,min(r['pressure_end_ns'],e['native_end_ns'])-max(r['pressure_start_ns'],e['native_start_ns']))
      assert overlap==r['native_overlap_ns']
      v.update(native_wall_ms=e['native_wall_ns']/1e6,native_cpu_ms=e['native_cpu_ns']/1e6,
               native_overlap_ms=overlap/1e6,native_overlap_fraction=overlap/e['native_wall_ns'])
     else:assert w['native'] is None and r['reset'] is None and r['native_overlap_ns']==0
     for k in ['pressure','reset','release','concurrent']:v[k+'_ms']=r[k+'_ns']/1e6
     if diag:
      assert r['initial']==r['final']=={'2':8192}
      assert r['at_use']=={str(0 if active else 2):8192}
      assert r['pressure_residency']==[{'0':1024}]*8
    else:
     assert r['fresh_buffer_bytes']==33554432  # 包含DRAM ID10；不许目标进池。
     if r['step']==0:assert r['pool_new_bytes']==107118592 and r['pool_reused_bytes']==0
     else:assert r['pool_new_bytes']==0 and r['pool_reused_bytes']==107118592
     assert r['moved_bytes']==(33554432 if active else 0)
     assert r['task_count']==r['migrations']==len(r['task_timeline'])==int(active)
     assert r['step_ns']==r['forward_ns']+r['backward_ns']+r['optimizer_and_drain_ns']
     for k in ['forward','backward','wait']:v[k+'_ms']=r[k+'_ns']/1e6
     if p.get('measure_target_pack',False):
      assert len(r['target_pack_records'])==1
      pack=r['target_pack_records'][0]
      assert pack['id']==10 and pack['node']==(0 if c=='dram' else 2) and pack['bytes']==33554432
      assert r['start_ns']<=pack['start_ns']<=pack['end_ns']<=r['forward_end_ns']
      assert pack['duration_ns']==pack['end_ns']-pack['start_ns'] and pack['minor_faults']>=8192
      v['target_pack_ms']=pack['duration_ns']/1e6
      v['target_pack_faults']=pack['minor_faults']
      v['forward_without_target_pack_ms']=(r['forward_ns']-pack['duration_ns'])/1e6
     v['late_per_step']=r['late_unpacks']
     if active:
      e=r['task_timeline'][0];native(e)
      assert e['id']==10 and e['bytes']==33554432 and e['destination']==0
      if r['step']>0:assert r['calibrated_schedule'] and r['ranked_submissions']==1
      v.update(native_wall_ms=e['native_wall_ns']/1e6,native_cpu_ms=e['native_cpu_ns']/1e6)
    if r['measured']:values.append(v)
   for k in values[0]:process[c].setdefault(k,[]).append(st.mean(v[k] for v in values))
   process[c].setdefault('amortized_run_ms',[]).append(timing['total_run_ns']/len(rows)/1e6)
   process[c].setdefault('setup_ms',[]).append(timing['setup_ns']/1e6)
   process[c].setdefault('teardown_ms',[]).append(timing['teardown_ns']/1e6)
   if diag and kind=='placement':
    corr=read(d/'correctness.json');assert corr['pass_'] and corr['mappings_released'] and corr['max_abs_error']==0
    for block in read(d/'events.json'):
     initial=[e for e in block['events'] if e['event']=='initial' and e['id']==10]
     used=[e for e in block['events'] if e['event']=='unpack' and e['id']==10]
     assert len(initial)==1 and initial[0]['nodes']=={str(0 if c=='dram' else 2):8192}
     assert used and all(e['nodes']=={str(2 if c=='cxl' else 0):8192} for e in used)
   checks.append(name)
 return dict(kind=kind,diagnostic=diag,source_hashes=hashes,process_means=process,
             means={c:{k:st.mean(v) for k,v in data.items()} for c,data in process.items()},
             runs_checked=checks,loss=loss,selection=selection,environment=env)
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('input',type=Path);p.add_argument('--output',type=Path,required=True)
 p.add_argument('--diagnostic-reference',type=Path);a=p.parse_args()
 if a.output.exists():p.error('output exists')
 s=audit(a.input)
 if not s['diagnostic']:
  assert a.diagnostic_reference is not None
  d=audit(a.diagnostic_reference)
  assert d['diagnostic'] and d['kind']==s['kind'] and d['source_hashes']==s['source_hashes']
  if s['kind']=='placement':assert s['loss'][:3]==d['loss'] and s['selection'][:3]==d['selection']
  data=s['process_means']
  pairs=([('fresh_migrate','fresh_nomigrate'),('reuse_migrate','reuse_nomigrate'),('fresh_migrate','reuse_migrate')]
         if s['kind']=='pressure' else [('cxl','dram'),('prefetch','cxl'),('prefetch','dram')])
  s['contrasts']={left+'-'+right:{k:estimate([x-y for x,y in zip(data[left][k],data[right][k])])
                    for k in data[left] if k in data[right]} for left,right in pairs}
  if s['kind']=='pressure':
   s['difference_in_differences']={k:estimate([a-b-c+d for a,b,c,d in zip(
       data['fresh_migrate'][k],data['fresh_nomigrate'][k],data['reuse_migrate'][k],data['reuse_nomigrate'][k])])
       for k in ['step_ms','pressure_ms','concurrent_ms','minor_faults','amortized_run_ms']}
 s['limitations']='三独立进程均值，配对t(df=2)区间未校正多重比较；pressure固定写入量，重叠时间可能不同；DRAM参考并非数学上界；无容量约束'
 a.output.mkdir(parents=True,exist_ok=False)
 (a.output/'summary.json').write_text(json.dumps(s,indent=2,ensure_ascii=False)+'\n')
 (a.output/'analyzer.py').write_bytes(Path(__file__).read_bytes())
 (a.output/'completed.json').write_text(json.dumps(dict(audit_passed=True,runs=len(s['runs_checked'])))+'\n')
 print(json.dumps(dict(means=s['means'],contrasts=s.get('contrasts'),difference_in_differences=s.get('difference_in_differences')),indent=2))
if __name__=='__main__':main()
