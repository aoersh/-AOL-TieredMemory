import gc
import threading
import unittest
import weakref
from unittest.mock import patch
from access_paths import Paths, Buffer, torch


class AsyncTests(unittest.TestCase):
    def test_repeated_unpack_and_release(self):
        paths=Paths(torch.nn.Identity(),'async',True)
        try:
            x=torch.randn(4096)
            item=paths.pack(x)
            ref=weakref.ref(item.buffer.mapping)
            torch.testing.assert_close(paths.unpack(item),x)
            torch.testing.assert_close(paths.unpack(item),x)
            paths.drain()
            self.assertEqual(len(paths.futures),1)
            self.assertEqual(paths.moved_bytes,16384)
            del item
            gc.collect()
            self.assertIsNone(ref())
        finally: paths.close()

    def test_worker_failure_reaches_demand(self):
        paths=Paths(torch.nn.Identity(),'async',True)
        try:
            with patch.object(Buffer,'migrate',side_effect=OSError('injected worker error')):
                item=paths.pack(torch.ones(4096))
                with self.assertRaisesRegex(OSError,'injected'):
                    paths.unpack(item)
                self.assertFalse(item.migrated)
                item.buffer.query(2)
        finally: paths.close()

    def test_inflight_task_owns_buffer_until_done(self):
        paths=Paths(torch.nn.Identity(),'async',True)
        started,release=threading.Event(),threading.Event()
        original=Buffer.migrate
        def blocked(buffer,*args,**kwargs):
            started.set()
            if not release.wait(5): raise RuntimeError('test gate timeout')
            return original(buffer,*args,**kwargs)
        try:
            with patch.object(Buffer,'migrate',blocked):
                item=paths.pack(torch.ones(4096))
                ref=weakref.ref(item.buffer.mapping)
                self.assertTrue(started.wait(5))
                del item
                gc.collect()
                self.assertIsNotNone(ref())
                release.set()
                paths.drain()
            gc.collect()
            self.assertIsNone(ref())
        finally:
            release.set()
            paths.close()


if __name__=='__main__':
    torch.set_num_threads(1)
    unittest.main(verbosity=2)
