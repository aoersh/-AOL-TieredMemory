"""C 内计时、errno 传播、页面状态和重复解包记账。"""
import ctypes as C
import errno
import unittest
from access_paths import Paths,torch
from numa_buffer import Buffer,lib,load_meter,NativeSample,checked

class NativeMeterTests(unittest.TestCase):
    def test_native_moves_and_same_node(self):
        for action,node in [('real_native',0),('real_vectorized_native',0),('same_vectorized_native',2)]:
            paths=Paths(torch.nn.Identity(),'ranked',migration_action=action)
            try:
                item=paths.pack(torch.arange(8192,dtype=torch.float32))
                value=paths.unpack(item);paths.unpack(item)
                self.assertEqual(len(paths.events),1)
                e=paths.events[0]
                self.assertEqual(paths.moved_bytes,0 if node==2 else item.buffer.size)
                keys=['syscall_start_ns','native_start_ns','native_end_ns','syscall_end_ns']
                self.assertEqual([e[k] for k in keys],sorted(e[k] for k in keys))
                self.assertEqual(e['syscall_ns'],e['native_pre_ns']+e['native_wall_ns']+e['native_post_ns'])
                self.assertGreater(e['native_cpu_ns'],0)
                self.assertGreater(e['native_tid'],0)
                item.buffer.query(node)
                torch.testing.assert_close(value,torch.arange(8192,dtype=torch.float32))
            finally:paths.close()

    def test_errno_preserved(self):
        b=Buffer(torch.ones(1024),2,verify=False)
        count,pages,nodes,status=b.vectorized_arguments(0)
        sample=NativeSample()
        result=load_meter().measured_move(C.cast(lib.move_pages,C.c_void_p),0,count,pages,nodes,status,0x40000000,C.byref(sample))
        self.assertEqual(result,-1)
        with self.assertRaises(OSError) as err:checked(result)
        self.assertEqual(err.exception.errno,errno.EINVAL)
        b.query(2)

if __name__=='__main__':
    torch.set_num_threads(1)
    unittest.main(verbosity=2)
