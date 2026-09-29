"""固定延迟时机控制：不搬页、时间顺序、错误传播。"""
import unittest
from unittest.mock import patch
from access_paths import Paths, Buffer, torch
from numa_buffer import lib

class TimingControlTests(unittest.TestCase):
    def test_delayed_control_keeps_cxl(self):
        paths=Paths(torch.nn.Identity(),'ranked',migration_action='prepare_vectorized',migration_delay_ns=5500000)
        try:
            item=paths.pack(torch.ones(8192))
            with patch.object(Buffer,'bind',side_effect=AssertionError('bind')), patch.object(lib,'move_pages',side_effect=AssertionError('move')):
                value=paths.unpack(item)
                paths.unpack(item)
            event=paths.events[0]
            self.assertEqual(len(paths.events),1)
            self.assertEqual(paths.moved_bytes,0)
            self.assertEqual(event['requested_delay_ns'],5500000)
            self.assertGreaterEqual(event['delay_ns'],5500000)
            self.assertLessEqual(event['ready_ns'],event['prepare_start_ns'])
            self.assertLessEqual(event['prepare_end_ns'],event['delay_start_ns'])
            self.assertLessEqual(event['delay_end_ns'],event['done_ns'])
            self.assertNotIn('syscall_start_ns',event)
            item.buffer.query(2)
            torch.testing.assert_close(value,torch.ones(8192))
        finally:paths.close()

    def test_delayed_real_timestamps(self):
        paths=Paths(torch.nn.Identity(),'ranked',migration_action='real_vectorized',migration_delay_ns=5500000)
        try:
            for iteration in range(2):
                paths.begin();item=paths.pack(torch.ones(8192));paths.before_backward()
                value=paths.unpack(item);paths.drain()
                e=paths.events[0]
                self.assertEqual(paths.moved_bytes,item.buffer.size)
                self.assertGreaterEqual(e['delay_ns'],5500000)
                keys=['ready_ns','submit_ns','start_ns','prepare_start_ns','prepare_end_ns','delay_start_ns','delay_end_ns','syscall_start_ns','syscall_end_ns','done_ns']
                self.assertEqual([e[k] for k in keys],sorted(e[k] for k in keys))
                self.assertEqual(e['syscall_ns'],e['syscall_end_ns']-e['syscall_start_ns'])
                item.buffer.query(0)
                torch.testing.assert_close(value,torch.ones(8192))
        finally:paths.close()

    def test_invalid_delays_rejected_before_binding(self):
        buffer=Buffer(torch.ones(8192),2,verify=False)
        with patch.object(Buffer,'bind',side_effect=AssertionError('bind')):
            for action,delay in [('real',5500000),('noop',5500000),('real_vectorized',-1)]:
                with self.assertRaises(ValueError):buffer.migration_probe(0,action,delay_ns=delay)
        buffer.query(2)

if __name__=='__main__':
    torch.set_num_threads(1)
    unittest.main(verbosity=2)
