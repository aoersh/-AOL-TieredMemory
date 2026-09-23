"""就绪任务优先级、前缀变化和工作者异常的检查。"""
import gc
import os
import threading
import unittest
import weakref
from unittest.mock import patch
from access_paths import Paths, PriorityWorker, Buffer, torch


class RankedTests(unittest.TestCase):
    def test_ready_queue_reorders_and_cpu_affinity(self):
        worker = PriorityWorker()
        started, release = threading.Event(), threading.Event()
        order = []
        def blocked():
            started.set()
            if not release.wait(5): raise RuntimeError('test timeout')
        try:
            hold = worker.submit(0, blocked)
            self.assertTrue(started.wait(5))
            later = worker.submit(8, lambda: order.append(8))
            earlier = worker.submit(1, lambda: order.append(1))
            release.set()
            for f in (hold, later, earlier): f.result()
            self.assertEqual(order, [1, 8])
            self.assertEqual(worker.submit(0, lambda: os.sched_getaffinity(0)).result(), {8})
        finally:
            release.set()
            worker.shutdown()

    def test_learn_at_pack_and_prefix_mismatch(self):
        paths = Paths(torch.nn.Identity(), 'ranked', True)
        x, y = torch.ones(4096), torch.ones(8192)
        try:
            first, second = paths.pack(x), paths.pack(y)
            self.assertIsNone(first.future)
            paths.before_backward()
            paths.unpack(second); paths.unpack(first); paths.drain()
            self.assertEqual(paths.previous_order, [2, 1])
            paths.begin()
            first = paths.pack(x)
            self.assertIsNotNone(first.future)  # Submitted before backward boundary.
            changed = paths.pack(torch.ones(16))
            self.assertIsNone(changed.future)
            paths.before_backward()
            self.assertFalse(paths.schedule_used)
            torch.testing.assert_close(paths.unpack(first), x)
            paths.unpack(first)
            paths.unpack(changed)
            paths.drain()
            self.assertEqual(paths.moved_bytes, 16384 + 4096)
            reference = weakref.ref(first.buffer.mapping)
            del first
            gc.collect()
            self.assertIsNone(reference())
        finally:
            paths.close()

    def test_ranked_error_propagates_and_worker_survives(self):
        paths = Paths(torch.nn.Identity(), 'ranked', True)
        try:
            item = paths.pack(torch.ones(4096))
            paths.before_backward(); paths.unpack(item); paths.drain(); paths.begin()
            with patch.object(Buffer, 'migrate', side_effect=OSError('injected ranked error')):
                item = paths.pack(torch.ones(4096))
                with self.assertRaisesRegex(OSError, 'injected'):
                    paths.unpack(item)
                self.assertEqual(paths.priority_worker.submit(0, lambda: 42).result(), 42)
        finally:
            paths.close()


if __name__ == '__main__':
    torch.set_num_threads(1)
    unittest.main(verbosity=2)
