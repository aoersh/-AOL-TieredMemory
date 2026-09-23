"""上一轮需求顺序调度的边界检查。"""
import unittest
from unittest.mock import patch
from access_paths import Paths, Buffer, torch


class DemandTests(unittest.TestCase):
    def test_learns_previous_order_and_changes_fallback(self):
        paths = Paths(torch.nn.Identity(), 'demand', True)
        try:
            sources = [torch.randn(4096), torch.randn(8192)]
            items = [paths.pack(x) for x in sources]
            paths.before_backward()
            self.assertFalse(paths.schedule_used)
            for i in (1, 0):
                torch.testing.assert_close(paths.unpack(items[i]), sources[i])
            paths.drain()
            self.assertEqual(paths.previous_order, [2, 1])
            paths.begin()
            items = [paths.pack(x) for x in sources]
            paths.before_backward()
            self.assertTrue(paths.schedule_used)
            self.assertEqual([f.result()['id'] for f in paths.futures], [2, 1])
            for i in (1, 0, 1):
                torch.testing.assert_close(paths.unpack(items[i]), sources[i])
            paths.drain()
            self.assertEqual(paths.moved_bytes, 12288 * 4)
            paths.begin()
            changed = paths.pack(torch.randn(16))
            paths.before_backward()
            self.assertFalse(paths.schedule_used)
            self.assertIsNone(changed.future)
            paths.unpack(changed)
            paths.drain()
        finally:
            paths.close()

    def test_failure_propagates_with_learned_schedule(self):
        paths = Paths(torch.nn.Identity(), 'demand', True)
        try:
            item = paths.pack(torch.ones(4096))
            paths.before_backward()
            paths.unpack(item)
            paths.drain()
            paths.begin()
            item = paths.pack(torch.ones(4096))
            with patch.object(Buffer, 'migrate', side_effect=OSError('injected demand worker failure')):
                paths.before_backward()
                with self.assertRaisesRegex(OSError, 'injected'):
                    paths.unpack(item)
                self.assertFalse(item.migrated)
        finally:
            paths.close()


if __name__ == '__main__':
    torch.set_num_threads(1)
    unittest.main(verbosity=2)
