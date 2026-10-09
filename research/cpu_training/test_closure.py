"""防止DRAM目标进池、fresh重复复制与nomigrate命名回归。"""
import gc
import unittest
import weakref
from unittest.mock import patch
from observe_saved_tensors import torch
from access_paths import Paths
from run_alloc_pressure import copy_pressure,release_buffers
from run_closure_matrix import CONDITIONS

class ClosureTests(unittest.TestCase):
 def test_target_fresh_on_both_nodes(self):
  for node in [0,2]:
   paths=Paths(torch.nn.Identity(),'direct',True,[1],buffer_policy='reuse_dram',initial_target_node=node,measure_target_pack=True)
   try:
    for _ in range(2):
     paths.begin()
     x=torch.arange(8192,dtype=torch.float32);y=torch.ones(4096)
     target=paths.pack(x);other=paths.pack(y)
     self.assertNotIn(1,paths.pool.slots);self.assertIn(2,paths.pool.slots)
     ref=weakref.ref(target.buffer.mapping)
     target.buffer.query(node)
     torch.testing.assert_close(paths.unpack(target),x)
     self.assertEqual(paths.fresh_buffer_bytes,32768)
     self.assertEqual(len(paths.target_pack_records),1)
     record=paths.target_pack_records[0]
     self.assertEqual((record['id'],record['node'],record['bytes']),(1,node,32768))
     self.assertEqual(record['duration_ns'],record['end_ns']-record['start_ns'])
     self.assertGreater(record['duration_ns'],0)
     self.assertGreaterEqual(record['minor_faults'],8)
     del target,other
     gc.collect();self.assertIsNone(ref());paths.pool.assert_idle()
   finally:paths.close()
 def test_pressure_exactly_one_copy(self):
  original=torch.Tensor.copy_;count=[]
  def counted(t,src,*args,**kwargs):
   count.append(src.numel()*src.element_size())
   return original(t,src,*args,**kwargs)
  source=torch.arange(8192,dtype=torch.float32).reshape(8,-1)
  with patch.object(torch.Tensor,'copy_',counted):
   buffers=copy_pressure(source,None)
  self.assertEqual(sum(count),32768);self.assertEqual(len(count),8)
  count.clear();source.add_(1)
  with patch.object(torch.Tensor,'copy_',counted):self.assertIs(copy_pressure(source,buffers),buffers)
  self.assertEqual(sum(count),32768);self.assertEqual(len(count),8)
  for b,x in zip(buffers,source):torch.testing.assert_close(b.tensor,x);b.query(0)
  maps=[b.mapping for b in buffers]
  release_buffers(buffers)
  self.assertTrue(all(m.closed for m in maps));self.assertEqual(buffers,[])
 def test_condition_names(self):
  self.assertEqual([c for c in CONDITIONS['pressure'] if c in ['fresh_migrate','reuse_migrate']],
                   ['fresh_migrate','reuse_migrate'])

if __name__=='__main__':
 torch.set_num_threads(1)
 unittest.main(verbosity=2)
