"""Run explicitly: uses this host's DRAM 0 and CXL 2, no global sysctl writes."""
import gc
import io
import unittest
import weakref
from unittest.mock import patch

from observe_saved_tensors import Observer, torch


class Twice(torch.autograd.Function):
    @staticmethod
    def forward(ctx, x):
        ctx.save_for_backward(x)
        return x.square()

    @staticmethod
    def backward(ctx, grad):
        x, = ctx.saved_tensors
        again, = ctx.saved_tensors
        return grad * (x + again)


class Boundaries(unittest.TestCase):
    def observer(self, mode='clone'):
        return Observer(torch.nn.Identity(), io.StringIO(), mode)

    def test_views_and_repeated_storage(self):
        o = self.observer()
        x = torch.randn(8, 8)
        packed = [o.pack(x), o.pack(x), o.pack(x.t()), o.pack(x[1:])]
        self.assertEqual(o.counts, dict(candidate=1, repeated_storage=1,
                                       noncontiguous=1, partial_storage=1))
        for p, expected in zip(packed, (x, x, x.t(), x[1:])):
            torch.testing.assert_close(o.unpack(p), expected)

    def test_address_reuse_is_not_storage_identity(self):
        o = self.observer()
        x = torch.randn(32)
        old = torch.randn(32).untyped_storage()
        o.seen[(0, x.untyped_storage().data_ptr())] = weakref.ref(old)
        o.pack(x)
        self.assertEqual(o.counts, {'candidate': 1})

    def test_live_source_inplace_rejected(self):
        for mode in ('observe', 'clone', 'dram', 'cxl', 'prefetch_sync'):
            with self.subTest(mode=mode):
                o = self.observer(mode)
                x = torch.ones(4096)
                p = o.pack(x)
                x.add_(1)
                with self.assertRaisesRegex(RuntimeError, 'in-place'):
                    o.unpack(p)

    def test_multiple_unpack_migrates_once_and_releases(self):
        o = self.observer('prefetch_sync')
        x = torch.randn(4096)
        p = o.pack(x)
        refs = [weakref.ref(p.buffer), weakref.ref(p.buffer.mapping), weakref.ref(p.value)]
        with patch.object(p.buffer, 'migrate', wraps=p.buffer.migrate) as migrate:
            torch.testing.assert_close(o.unpack(p), x)
            torch.testing.assert_close(o.unpack(p), x)
            self.assertEqual(migrate.call_count, 1)
        del migrate, p
        gc.collect()
        self.assertTrue(all(ref() is None for ref in refs))

    def test_repeated_autograd_unpack_gradient(self):
        for mode in ('observe', 'clone', 'dram', 'cxl', 'prefetch_sync'):
            with self.subTest(mode=mode):
                o = self.observer(mode)
                x = torch.randn(4096, requires_grad=True)
                with torch.autograd.graph.saved_tensors_hooks(o.pack, o.unpack):
                    loss = Twice.apply(x).sum()
                    loss.backward()
                torch.testing.assert_close(x.grad, 2 * x.detach())
                del loss
                gc.collect()
                self.assertTrue(all(ref() is None for ref in o.refs + o.mapping_refs))

    def test_migration_failure_propagates(self):
        o = self.observer('prefetch_sync')
        p = o.pack(torch.ones(4096))
        with patch.object(p.buffer, 'migrate', side_effect=OSError('injected migration failure')):
            with self.assertRaisesRegex(OSError, 'injected'):
                o.unpack(p)
        self.assertEqual(p.node, 2)
        p.buffer.query(2)


if __name__ == '__main__':
    torch.set_num_threads(1)
    unittest.main(verbosity=2)
