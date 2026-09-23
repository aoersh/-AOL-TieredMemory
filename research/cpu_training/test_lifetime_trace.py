"""验证跨步记录及 tensor wrapper 释放后仍存活的 storage 别名。"""
import gc
import unittest
from access_paths import Paths, torch
from run_access_paths import run

class LifetimeTests(unittest.TestCase):
    def test_storage_alias_keeps_mapping_alive(self):
        paths = Paths(torch.nn.Identity(), 'direct', trace=True)
        try:
            item = paths.pack(torch.ones(4096))
            record = item.lifetime
            value = paths.unpack(item)
            paths.unpack(item)
            alias = value.view(-1)
            del item, value
            gc.collect()
            self.assertIsNone(record['release_ns'])
            self.assertEqual(len(record['unpack_times_ns']), 2)
            del alias
            gc.collect()
            self.assertGreaterEqual(record['release_ns'], record['unpack_times_ns'][-1])
        finally:
            paths.close()

    def test_all_iterations_and_optional_trace(self):
        model = torch.nn.Sequential(torch.nn.Linear(4, 4), torch.nn.GELU())
        x, y = torch.ones(2, 4), torch.zeros(2, 4)
        rows, _, _, records = run(model, x, y, 'direct', 3, 1, False, trace=True)
        self.assertEqual({r['step'] for r in records}, {0, 1, 2, 3})
        self.assertTrue(all(r['pack_ns'] <= r['unpack_ns'] <= r['release_ns'] for r in records))
        self.assertEqual({r['step'] for r in records if r['measured']}, {1, 2, 3})
        self.assertEqual(run(model, x, y, 'direct', 1, 0, False)[3], [])

if __name__ == '__main__':
    torch.set_num_threads(1)
    unittest.main(verbosity=2)
