import gc,unittest
from run_capacity_pilot import CapacityPaths,torch
class CapacityTests(unittest.TestCase):
 def test_alias_holds_capacity_and_releases_for_next_object(self):
  class TestPaths(CapacityPaths):policy='pack';capacity=32768;audit_rows=[]
  p=TestPaths(torch.nn.Identity(),'direct',True)
  try:
   x=torch.ones(8192);item=p.pack(x);alias=p.unpack(item).view(-1)
   self.assertEqual(p.live,32768)
   del item;gc.collect();self.assertEqual(p.live,32768)
   blocked=p.pack(torch.ones(8192));self.assertEqual(blocked.node,2)
   del alias;gc.collect();self.assertEqual(p.live,0)
   next_item=p.pack(torch.ones(8192));self.assertEqual(next_item.node,0)
   with self.assertRaises(AssertionError):p.begin()
   del blocked,next_item;gc.collect();self.assertEqual(p.live,0)
  finally:p.close()
 def test_zero_budget_never_migrates(self):
  class TestPaths(CapacityPaths):policy='demand';capacity=0;audit_rows=[]
  p=TestPaths(torch.nn.Identity(),'direct',True)
  try:
   item=p.pack(torch.ones(8192));p.unpack(item)
   self.assertEqual(item.node,2);self.assertEqual(p.moved_bytes,0)
   del item;gc.collect()
  finally:p.close()
 def test_explicit_object_selection_and_repeated_unpack(self):
  for policy in ['pack','demand']:
   class TestPaths(CapacityPaths):
    capacity=32768;selected_ids={2};audit_rows=[]
   TestPaths.policy=policy
   p=TestPaths(torch.nn.Identity(),'direct',True)
   try:
    a=p.pack(torch.ones(8192));b=p.pack(torch.arange(8192,dtype=torch.float32))
    self.assertEqual(a.node,2)
    result=p.unpack(b);torch.testing.assert_close(result,torch.arange(8192,dtype=torch.float32))
    self.assertEqual(b.node,0);self.assertEqual(p.live,32768)
    p.unpack(b);self.assertEqual(p.moved_bytes,32768 if policy=='demand' else 0)
    del a,b,result;gc.collect();self.assertEqual(p.live,0)
   finally:p.close()
if __name__=='__main__':
 torch.set_num_threads(1);unittest.main(verbosity=2)
