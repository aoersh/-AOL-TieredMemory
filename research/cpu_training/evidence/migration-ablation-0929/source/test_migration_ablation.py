"""验证控制组不搬页、重复需求不重复记账及真实迁移失败传播。"""
import unittest
from unittest.mock import patch
from access_paths import Paths,Buffer,torch
from numa_buffer import lib

class AblationTests(unittest.TestCase):
    def test_controls_do_not_call_bind_or_move(self):
        for action in ['noop','prepare']:
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
        paths=Paths(torch.nn.Identity(),'ranked',migration_action='real')
        try:
            item=paths.pack(torch.ones(8192))
            with patch.object(lib,'move_pages',side_effect=OSError('injected')):
                with self.assertRaisesRegex(OSError,'injected'): paths.unpack(item)
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
