"""Ample-capacity path pilot: eager-at-pack async migration, not a deadline policy."""
import os
import time
import weakref
import queue
import threading
from concurrent.futures import Future
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from observe_saved_tensors import torch
from numa_buffer import Buffer

class PriorityWorker:
    """Single CPU-8 worker; only queued tasks can be reordered, never in-flight work."""
    def __init__(self):
        self.tasks = queue.PriorityQueue()
        self.sequence = 0
        self.closed = False
        self.ready = Future()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        self.ready.result(timeout=10)

    def run(self):
        try:
            os.sched_setaffinity(0, {8})
        except BaseException as error:
            self.ready.set_exception(error)
            return
        self.ready.set_result(None)
        while True:
            priority, sequence, future, function, args = self.tasks.get()
            if function is None:
                future.set_result(None)
                return
            if future.set_running_or_notify_cancel():
                try:
                    result = function(*args)
                except BaseException as error:
                    future.set_exception(error)
                else:
                    future.set_result(result)
                    del result
            # Drop task-owned mappings before waiting for the next queue entry.
            del args, function, future

    def submit(self, priority, function, *args):
        if self.closed:
            raise RuntimeError('migration worker is closed')
        future = Future()
        self.sequence += 1
        self.tasks.put((priority, self.sequence, future, function, args))
        return future

    def barrier(self):
        self.submit(float('inf'), lambda: None).result()

    def shutdown(self):
        if self.closed:
            return
        done = self.submit(float('inf'), None)
        self.closed = True
        done.result()
        self.thread.join()


class Paths:
    def __init__(self, model, mode, diagnostic=False, target_ids=None, budget_bytes=None, trace=False):
        self.trace = trace
        self.mode, self.diagnostic = mode, diagnostic
        self.target_ids = set(target_ids or [])
        self.budget_bytes = budget_bytes
        self.budget_reserved = 0
        self.parameters = {p.untyped_storage().data_ptr() for p in model.parameters()}
        self.executor = None if mode in ('async', 'ranked', 'budget') else ThreadPoolExecutor(max_workers=1, initializer=lambda: os.sched_setaffinity(0, {8}))
        self.priority_worker = PriorityWorker() if mode in ('async', 'ranked', 'budget') else None
        if self.executor:
            self.executor.submit(lambda: None).result()  # Same reserved worker for every policy.
        self.previous_order = []
        self.previous_selection = None
        self.iteration = -1
        self.begin()

    def begin(self):
        self.iteration += 1
        self.seen, self.futures, self.refs, self.events, self.selection = {}, [], [], [], []
        self.count = self.moved_bytes = self.wait_ns = self.late = 0
        self.lifetimes = []
        self.budget_reserved = 0
        self.pending, self.demand_order = {}, []
        self.calibrating = self.mode in ('demand', 'ranked') and self.previous_selection is None
        self.prefix_valid = self.previous_selection is not None
        self.rank = {identity: index for index, identity in enumerate(self.previous_order)}
        self.ranked_submissions = 0
        self.schedule_used = False

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
        if self.mode == 'ranked' and self.prefix_valid:
            self.prefix_valid = (self.count <= len(self.previous_selection) and
                                 self.selection[-1] == self.previous_selection[self.count - 1])
        if reason == 'candidate':
            item.node = (2 if self.mode in ('direct', 'sync', 'async', 'demand', 'ranked', 'budget')
                         and (not self.target_ids or self.count in self.target_ids) else 0)
            if self.mode == 'dram':
                item.node = 0
            if self.mode == 'direct' and item.node == 0:
                # Non-target candidates remain in DRAM for single-object isolation.
                pass
            node = item.node
            item.buffer = Buffer(tensor, node, verify=self.diagnostic)
            item.value = item.buffer.tensor
            if self.trace:
                record = dict(step=self.iteration - 1, id=item.identity,
                              address=item.value.data_ptr(), bytes=item.buffer.size,
                              pack_ns=time.monotonic_ns(), unpack_ns=None,
                              unpack_times_ns=[], release_ns=None,
                              shape=list(tensor.shape), dtype=str(tensor.dtype), node=node)
                self.lifetimes.append(record)
                item.lifetime = record
                # Callback retains metadata only, never the mapping or tensor.
                weakref.finalize(item.buffer.mapping, self.mark_release, record)
            if self.diagnostic:
                self.refs.extend([weakref.ref(item.buffer), weakref.ref(item.buffer.mapping)])
                self.events.append(dict(event='initial', id=item.identity, nodes=item.buffer.query(node)))
            if self.mode == 'budget' and item.node == 2:
                item.prefetch = self.budget_bytes is None or self.budget_reserved + item.buffer.size <= self.budget_bytes
                if item.prefetch:
                    self.budget_reserved += item.buffer.size
                    item.future = self.priority_worker.submit(0, self.migrate, item.buffer, item.identity, time.monotonic_ns())
                    self.futures.append(item.future)
            elif self.mode == 'async' and item.node == 2:
                item.future = self.priority_worker.submit(0, self.migrate, item.buffer, item.identity, time.monotonic_ns())
                self.futures.append(item.future)
            elif self.mode == 'ranked' and item.node == 2 and self.prefix_valid:
                item.future = self.priority_worker.submit(self.rank.get(item.identity, len(self.rank)),
                                                         self.migrate, item.buffer, item.identity, time.monotonic_ns())
                self.futures.append(item.future)
                self.ranked_submissions += 1
            elif self.mode == 'demand' and item.node == 2:
                self.pending[item.identity] = item
        return item

    def before_backward(self):
        """Submit using only the preceding iteration; changed traces use sync fallback."""
        if self.mode == 'ranked':
            self.schedule_used = self.prefix_valid and self.previous_selection == self.selection
            return
        if self.mode != 'demand':
            return
        if self.previous_selection == self.selection:
            rank = {identity: index for index, identity in enumerate(self.previous_order)}
            for identity in sorted(self.pending, key=lambda i: (rank.get(i, len(rank)), i)):
                item = self.pending[identity]
                item.future = self.executor.submit(self.migrate, item.buffer, identity, time.monotonic_ns())
                self.futures.append(item.future)
            self.schedule_used = True
        # Ownership belongs to autograd or submitted jobs, not a whole-step registry.
        self.pending.clear()

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
            if item.identity not in self.demand_order:
                self.demand_order.append(item.identity)
            if item.future is not None:
                demand = time.monotonic_ns()
                late = not item.future.done()
                record = item.future.result()  # Propagate worker failures before use.
                self.wait_ns += time.monotonic_ns() - demand
                self.late += int(late)
                if not item.migrated:
                    self.moved_bytes += record['bytes']
                    self.events.append(dict(record, demand_ns=demand, late=late))
                    item.migrated = True
                    item.node = 0
            elif self.mode in ('sync', 'demand', 'ranked') and item.node == 2 and not item.migrated:
                record = self.migrate(item.buffer, item.identity, time.monotonic_ns())
                self.wait_ns += record['done_ns'] - record['submit_ns']
                self.moved_bytes += record['bytes']
                self.events.append(record)
                item.migrated = True
                item.node = 0
            if self.diagnostic:
                    self.events.append(dict(event='unpack', id=item.identity,
                                        nodes=item.buffer.query(item.node)))
            if self.trace:
                timestamp = time.monotonic_ns()
                item.lifetime['unpack_times_ns'].append(timestamp)
                if item.lifetime['unpack_ns'] is None:
                    item.lifetime['unpack_ns'] = timestamp
        return item.value

    def drain(self):
        for future in self.futures:
            future.result()
        # Future completion precedes the worker's destruction of its work item.
        # A queue barrier also releases task-held buffer references before reuse.
        if self.executor:
            self.executor.submit(lambda: None).result()
        if self.priority_worker:
            self.priority_worker.barrier()
        if self.mode in ('demand', 'ranked'):
            self.previous_order = list(self.demand_order)
            self.previous_selection = list(self.selection)

    @staticmethod
    def mark_release(record):
        record["release_ns"] = time.monotonic_ns()

    def close(self):
        if self.executor:
            self.executor.shutdown(wait=True, cancel_futures=True)
        if self.priority_worker:
            self.priority_worker.shutdown()
        self.pending.clear()
