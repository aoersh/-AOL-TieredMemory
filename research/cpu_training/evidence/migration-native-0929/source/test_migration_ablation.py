"""验证控制组不搬页、重复需求不重复记账及真实迁移失败传播。"""
import unittest
from unittest.mock import patch
from access_paths import Paths,Buffer,torch
from numa_buffer import lib

class AblationTests(unittest.TestCase):
    def test_controls_do_not_call_bind_or_move(self):
        for action in ['noop','prepare','prepare_vectorized']:
            paths=Paths(torch.nn.Identity(),'ranked',migration_action=action)
            try:
                item=paths.pack(torch.ones(8192))
                with patch.object(Buffer,'bind',side_effect=AssertionError('bind')), patch.object(lib,'move_pages',side_effect=AssertionError('move')):
                    value=paths.unpack(item);paths.unpack(item)
                self.assertEqual(paths.moved_bytes,0)
                self.assertEqual(len(paths.events),1)
                self.assertEqual(paths.events[0]['event'],'control')
                item.buffer.query(2)
                torch.testing.assert_close(value,torch.ones(8192))
            finally:paths.close()

    def test_real_failure_propagates(self):
        for action in ['real', 'real_vectorized']:
            paths=Paths(torch.nn.Identity(),'ranked',migration_action=action)
            try:
                item=paths.pack(torch.ones(8192))
                with patch.object(lib,'move_pages',side_effect=OSError('injected')):
                    with self.assertRaisesRegex(OSError,'injected'): paths.unpack(item)
                self.assertEqual(paths.moved_bytes,0)
            finally:paths.close()

    def test_vectorized_addresses_and_ownership(self):
        import ctypes as C
        import gc
        # 非整页尺寸、多种 node、多个同时存活 buffer，检查无旧地址和截断。
        buffers=[Buffer(torch.ones(n),2,verify=False) for n in [1,1025,8192]]
        arrays=[]
        for buffer in buffers:
            for node in [0,2]:
                count,pages,nodes,status=buffer.vectorized_arguments(node)
                arrays.append((buffer,node,count,pages,nodes,status))
        gc.collect()
        for buffer,node,count,pages,nodes,status in arrays:
            self.assertEqual(list(pages),[buffer.address+i*buffer.page for i in range(count)])
            self.assertEqual(list(nodes),[node]*count)
            self.assertEqual(list(status),[0]*count)
            self.assertEqual(C.sizeof(pages),count*C.sizeof(C.c_void_p))

    def test_partial_vectorized_migration_rejected(self):
        paths=Paths(torch.nn.Identity(),'ranked',migration_action='real_vectorized')
        try:
            item=paths.pack(torch.ones(8192))
            def partial(pid,count,pages,nodes,status,flags):
                status[0]=-16
                return 1
            with patch.object(lib,'move_pages',side_effect=partial):
                with self.assertRaisesRegex(RuntimeError,'Partial migration'):paths.unpack(item)
            self.assertEqual(paths.moved_bytes,0)
        finally:paths.close()

    def test_learned_queue_keeps_control_on_cxl(self):
        paths=Paths(torch.nn.Identity(),'ranked',migration_action='prepare')
        try:
            for _ in range(2):
                paths.begin();item=paths.pack(torch.ones(8192));paths.before_backward()
                paths.unpack(item);paths.drain();item.buffer.query(2)
                self.assertEqual(paths.moved_bytes,0)
            self.assertEqual(paths.ranked_submissions,1)
        finally:paths.close()

if __name__=='__main__':
    torch.set_num_threads(1)
    unittest.main(verbosity=2)
