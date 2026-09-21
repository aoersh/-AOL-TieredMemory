"""Ample-capacity path pilot: eager-at-pack async migration, not a deadline policy."""
import os
import time
import weakref
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from observe_saved_tensors import torch
from numa_buffer import Buffer


class Paths:
    def __init__(self, model, mode, diagnostic=False):
        self.mode, self.diagnostic = mode, diagnostic
        self.parameters = {p.untyped_storage().data_ptr() for p in model.parameters()}
        self.executor = ThreadPoolExecutor(max_workers=1, initializer=lambda: os.sched_setaffinity(0, {8}))
        self.executor.submit(lambda: None).result()  # Same reserved worker for every policy.
        self.begin()

    def begin(self):
        self.seen, self.futures, self.refs, self.events, self.selection = {}, [], [], [], []
        self.count = self.moved_bytes = self.wait_ns = self.late = 0

    def pack(self, tensor):
        storage = tensor.untyped_storage()
        ptr = storage.data_ptr()
        reason = 'candidate'
        if not tensor.numel(): reason = 'empty'
        elif ptr in self.parameters: reason = 'parameter'
        elif not tensor.is_contiguous(): reason = 'noncontiguous'
        elif tensor.storage_offset() or tensor.numel()*tensor.element_size() != storage.nbytes(): reason = 'partial_storage'
        elif ptr in self.seen and self.seen[ptr]() is storage: reason = 'repeated_storage'
        self.seen[ptr] = weakref.ref(storage)
        self.count += 1
        item = SimpleNamespace(value=tensor.detach(), buffer=None, future=None, migrated=False,
                               identity=self.count, source=weakref.ref(tensor), version=tensor._version)
        self.selection.append((self.count, tuple(tensor.shape), reason))
        if reason == 'candidate':
            node = 0 if self.mode == 'dram' else 2
            item.buffer = Buffer(tensor, node, verify=self.diagnostic)
            item.value = item.buffer.tensor
            if self.diagnostic:
                self.refs.extend([weakref.ref(item.buffer), weakref.ref(item.buffer.mapping)])
                self.events.append(dict(event='initial', id=item.identity, nodes=item.buffer.query(node)))
            if self.mode == 'async':
                submitted = time.monotonic_ns()
                item.future = self.executor.submit(self.migrate, item.buffer, item.identity, submitted)
                self.futures.append(item.future)
        return item

    def migrate(self, buffer, identity, submitted):
        start = time.monotonic_ns()
        nodes = buffer.migrate(0, verify=self.diagnostic)
        return dict(event='migration', id=identity, submit_ns=submitted, start_ns=start,
                    done_ns=time.monotonic_ns(), bytes=buffer.size, nodes=nodes)

    def unpack(self, item):
        source = item.source()
        if source is not None and source._version != item.version:
            raise RuntimeError('In-place update of a live saved source is unsupported')
        if item.buffer:
            if self.mode == 'async':
                demand = time.monotonic_ns()
                late = not item.future.done()
                record = item.future.result()  # Propagate worker failures before use.
                self.wait_ns += time.monotonic_ns() - demand
                self.late += int(late)
                if not item.migrated:
                    self.moved_bytes += record['bytes']
                    self.events.append(dict(record, demand_ns=demand, late=late))
                    item.migrated = True
            elif self.mode == 'sync' and not item.migrated:
                record = self.migrate(item.buffer, item.identity, time.monotonic_ns())
                self.wait_ns += record['done_ns'] - record['submit_ns']
                self.moved_bytes += record['bytes']
                self.events.append(record)
                item.migrated = True
            if self.diagnostic:
                self.events.append(dict(event='unpack', id=item.identity,
                                        nodes=item.buffer.query(2 if self.mode == 'direct' else 0)))
        return item.value

    def drain(self):
        for future in self.futures:
            future.result()
        # Future completion precedes the worker's destruction of its work item.
        # A queue barrier also releases task-held buffer references before reuse.
        self.executor.submit(lambda: None).result()

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=True)
