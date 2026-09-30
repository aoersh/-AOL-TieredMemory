"""真实 node0/2 上验证复用 lease、别名、逐页驻留和 autograd 生命周期。"""
import gc
import unittest
import weakref
from access_paths import Paths, torch
from numa_buffer import DramBufferPool
from test_boundaries import Twice


class BufferReuseTests(unittest.TestCase):
    def test_alias_and_storage_hold_lease(self):
        for kind in ('view', 'detach', 'storage', 'numpy'):
            with self.subTest(kind=kind):
                pool = DramBufferPool()
                x = torch.arange(4096, dtype=torch.float32)
                b = pool.acquire(1, x, verify=True)
                address = b.address
                alias = {'view': lambda: b.tensor[10:20],
                         'detach': lambda: b.tensor.detach(),
                         'storage': lambda: b.tensor.untyped_storage(),
                         'numpy': lambda: b.tensor.numpy()}[kind]()
                del b
                gc.collect()
                with self.assertRaisesRegex(RuntimeError, 'live storage'):
                    pool.acquire(1, x+1)
                with self.assertRaisesRegex(RuntimeError, 'retained'):
                    pool.close()
                del alias
                gc.collect()
                pool.assert_idle()
                b = pool.acquire(1, x+1, verify=True)
                self.assertEqual(b.address, address)
                torch.testing.assert_close(b.tensor, x+1)
                mapping = b.mapping
                del b
                pool.close()
                self.assertTrue(mapping.closed)

    def test_shape_and_node_guard(self):
        pool = DramBufferPool()
        x = torch.ones(4096)
        b = pool.acquire(1, x)
        del b
        with self.assertRaisesRegex(RuntimeError, 'signature'):
            pool.acquire(1, x.reshape(64,64))
        with self.assertRaisesRegex(RuntimeError, 'node0'):
            pool.acquire(2, x, node=2)
        pool.close()
        with self.assertRaises(RuntimeError):
            pool.acquire(1, x)

    def test_repeated_unpack_autograd_release(self):
        paths = Paths(torch.nn.Identity(), 'direct', True, [10], buffer_policy='reuse_dram')
        try:
            for _ in range(3):
                paths.begin()
                x = torch.randn(4096, requires_grad=True)
                with torch.autograd.graph.saved_tensors_hooks(paths.pack, paths.unpack):
                    loss = Twice.apply(x).sum()
                    loss.backward()
                paths.drain()
                torch.testing.assert_close(x.grad, 2*x.detach())
                del loss
                paths.pool.assert_idle()
                self.assertTrue(all(ref() is None for ref in paths.refs))
        finally:
            paths.close()

    def test_live_source_update_rejected(self):
        paths = Paths(torch.nn.Identity(),'direct',True,[10],buffer_policy='reuse_dram')
        try:
            x = torch.ones(4096)
            item = paths.pack(x)
            x.add_(1)
            with self.assertRaisesRegex(RuntimeError, 'In-place'):
                paths.unpack(item)
            del item
        finally:
            paths.close()

    def test_noncontiguous_and_repeated_selection_preserved(self):
        paths = Paths(torch.nn.Identity(),'direct',True,[10],buffer_policy='reuse_dram')
        try:
            x = torch.randn(32,32)
            items = [paths.pack(x), paths.pack(x), paths.pack(x.t()), paths.pack(x[1:])]
            self.assertEqual([s[2] for s in paths.selection],
                             ['candidate','repeated_storage','noncontiguous','partial_storage'])
            self.assertEqual(len(paths.pool.slots),1)
            del items
        finally:
            paths.close()

    def test_cxl_target_not_retained_or_reused(self):
        paths = Paths(torch.nn.Identity(),'ranked',True,[1],
                      migration_action='real_vectorized_native',buffer_policy='reuse_dram')
        try:
            for _ in range(3):
                paths.begin()
                x = torch.ones(8192)
                item = paths.pack(x)
                ref = weakref.ref(item.buffer.mapping)
                self.assertEqual(paths.events[0]['nodes'], {2:8})
                paths.before_backward()
                paths.unpack(item)
                paths.unpack(item)
                paths.drain()
                item.buffer.query(0)
                self.assertEqual(paths.moved_bytes,32768)
                self.assertEqual(paths.fresh_buffer_bytes,32768)
                self.assertEqual(len(paths.pool.slots),0)
                del item
                gc.collect()
                self.assertIsNone(ref())
        finally:
            paths.close()


if __name__ == '__main__':
    torch.set_num_threads(1)
    unittest.main(verbosity=2)
