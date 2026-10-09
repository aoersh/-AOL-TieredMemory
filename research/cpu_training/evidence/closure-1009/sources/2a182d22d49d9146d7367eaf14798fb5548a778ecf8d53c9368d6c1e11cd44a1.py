"""Isolated mmap tensors for placement/correctness pilots; Linux libnuma ABI."""
import ctypes as C
import mmap
import os
import time
import weakref
from types import SimpleNamespace
from pathlib import Path

lib = C.CDLL('libnuma.so.1', use_errno=True)
lib.mbind.argtypes = [C.c_void_p, C.c_ulong, C.c_int, C.POINTER(C.c_ulong), C.c_ulong, C.c_uint]
lib.mbind.restype = C.c_long
lib.move_pages.argtypes = [C.c_int, C.c_ulong, C.POINTER(C.c_void_p),
                          C.POINTER(C.c_int), C.POINTER(C.c_int), C.c_int]
lib.move_pages.restype = C.c_long


class NativeSample(C.Structure):
    _fields_ = [(k,C.c_uint64) for k in ('start_ns','end_ns','cpu_ns')] + [(k,C.c_long) for k in ('voluntary','involuntary','tid')]

_meter = None

def load_meter():
    global _meter
    if _meter is None:
        path=Path(__file__).resolve().parents[2]/'.deps/training-native/libmigration_meter.so'
        _meter=C.CDLL(str(path),use_errno=True)
        _meter.measured_move.argtypes=[C.c_void_p,C.c_int,C.c_ulong,C.POINTER(C.c_void_p),C.POINTER(C.c_int),C.POINTER(C.c_int),C.c_int,C.POINTER(NativeSample)]
        _meter.measured_move.restype=C.c_long
    return _meter


def checked(result):
    if result < 0:
        error = C.get_errno()
        raise OSError(error, os.strerror(error))


class Buffer:
    def __init__(self, tensor, node, verify=True):
        import torch
        self.page = mmap.PAGESIZE
        self.size = ((tensor.numel() * tensor.element_size() + self.page - 1) // self.page) * self.page
        self.mapping = mmap.mmap(-1, self.size, flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)
        self.mapping.madvise(mmap.MADV_NOHUGEPAGE)
        self.address = C.addressof(C.c_char.from_buffer(self.mapping))
        self.bind(node)
        self.tensor = torch.frombuffer(self.mapping, dtype=tensor.dtype,
                                       count=tensor.numel()).reshape(tensor.shape)
        self.tensor.copy_(tensor.detach())
        if verify:
            self.query(node)

    def bind(self, node):
        mask = C.c_ulong(1 << node)
        checked(lib.mbind(self.address, self.size, 2, C.byref(mask), 64, 0))

    def pages(self):
        count = self.size // self.page
        return count, (C.c_void_p * count)(*(self.address + i * self.page for i in range(count)))

    def query(self, expected):
        count, pages = self.pages()
        status = (C.c_int * count)()
        checked(lib.move_pages(0, count, pages, None, status, 0))
        counts = {}
        for node in status:
            counts[node] = counts.get(node, 0) + 1
        if counts != {expected: count}:
            raise RuntimeError(f'Unexpected residency {counts}; expected node {expected}')
        return counts

    def migrate(self, node, verify=True):
        # Rebind to the destination before requesting migration of these private pages.
        self.bind(node)
        count, pages = self.pages()
        nodes, status = (C.c_int * count)(*([node] * count)), (C.c_int * count)()
        checked(lib.move_pages(0, count, pages, nodes, status, 2))
        if any(value != node for value in status):
            raise RuntimeError(f'Partial migration: {list(status)[:16]}')
        return self.query(node) if verify else {node: count}

    def vectorized_arguments(self, node):
        """每次按当前 buffer 地址构造，无地址缓存；分配和初始化仍在迁移任务内。"""
        import numpy as np
        count = self.size // self.page
        addresses = np.arange(count, dtype=np.uintp)
        addresses *= self.page
        addresses += self.address
        destinations = np.full(count, node, dtype=np.intc)
        # from_buffer 持有底层数组引用，直到同步 move_pages 调用返回。
        pages = (C.c_void_p * count).from_buffer(addresses)
        nodes = (C.c_int * count).from_buffer(destinations)
        status = (C.c_int * count)()
        return count, pages, nodes, status

    def migration_probe(self, node, action, verify=False, delay_ns=0):
        """可选消融；延迟只用于时机归因，完整纳入任务及训练计时。"""
        native = action.endswith('_native')
        same = action == 'same_vectorized_native'
        base = action.removesuffix('_native')
        if same: node = 2
        if base not in ('real', 'noop', 'prepare', 'real_vectorized', 'prepare_vectorized', 'same_vectorized'):
            raise ValueError(action)
        if delay_ns < 0 or (delay_ns and action not in ('real_vectorized','prepare_vectorized')):
            raise ValueError('delay requires vectorized action and nonnegative duration')
        times = dict(bind_ns=0, prepare_ns=0, delay_ns=0, syscall_ns=0, status_ns=0)
        if action == 'noop':
            return times
        if base in ('real', 'real_vectorized', 'same_vectorized'):
            t = time.monotonic_ns()
            self.bind(node)
            times['bind_ns'] = time.monotonic_ns() - t
        t = time.monotonic_ns()
        times['prepare_start_ns'] = t
        if base.endswith('_vectorized'):
            count, pages, nodes, status = self.vectorized_arguments(node)
        else:
            count, pages = self.pages()
            nodes, status = (C.c_int * count)(*([node] * count)), (C.c_int * count)()
        times['prepare_end_ns'] = time.monotonic_ns()
        times['prepare_ns'] = times['prepare_end_ns'] - t
        times['delay_start_ns'] = time.monotonic_ns()
        if delay_ns:
            time.sleep(delay_ns / 1e9)
        times['delay_end_ns'] = time.monotonic_ns()
        times['delay_ns'] = times['delay_end_ns'] - times['delay_start_ns']
        if base in ('prepare', 'prepare_vectorized'):
            return times
        if native:
            meter=load_meter(); sample=NativeSample()
            function=C.cast(lib.move_pages,C.c_void_p)
        times['syscall_start_ns'] = time.monotonic_ns()
        if native:
            rc=meter.measured_move(function,0,count,pages,nodes,status,2,C.byref(sample))
            checked(rc)
        else:
            checked(lib.move_pages(0, count, pages, nodes, status, 2))
        times['syscall_end_ns'] = time.monotonic_ns()
        if native:
            times.update(native_start_ns=sample.start_ns,native_end_ns=sample.end_ns,
                         native_wall_ns=sample.end_ns-sample.start_ns,native_cpu_ns=sample.cpu_ns,
                         native_voluntary=sample.voluntary,native_involuntary=sample.involuntary,native_tid=sample.tid,
                         native_pre_ns=sample.start_ns-times['syscall_start_ns'],
                         native_post_ns=times['syscall_end_ns']-sample.end_ns)
        times['syscall_ns'] = times['syscall_end_ns'] - times['syscall_start_ns']
        t = time.monotonic_ns()
        if any(value != node for value in status):
            raise RuntimeError('Partial migration in probe')
        times['status_ns'] = time.monotonic_ns() - t
        if verify:
            self.query(node)
        return times


class DramBufferPool:
    """仅复用非目标 node0 副本；storage 最后释放后才能复用槽位。

    ctypes owner 由 torch.frombuffer 的 storage 持有，所有 view/detach 都共享
    这个所有权。不能用 Packed 或单个 Tensor 的销毁作为槽位可复用的依据。
    固定 trace 每个 saved ID 一个槽位；活跃 lease 或 shape 变化直接失败。
    """
    def __init__(self):
        self.slots = {}
        self.closed = False
        self.begin()

    def begin(self):
        self.new_bytes = self.reused_bytes = self.allocation_ns = self.materialize_ns = 0

    @staticmethod
    def release(slot):
        slot.busy = False

    def acquire(self, identity, tensor, node=0, verify=False):
        import torch
        if self.closed or node != 0:
            raise RuntimeError('DRAM pool only accepts node0 while open')
        signature = (tuple(tensor.shape), tensor.dtype, node)
        slot = self.slots.get(identity)
        is_new = slot is None
        acquisition_start = time.monotonic_ns() if is_new else 0
        buffer = Buffer.__new__(Buffer)
        buffer.page = mmap.PAGESIZE
        buffer.size = ((tensor.numel()*tensor.element_size()+buffer.page-1)//buffer.page)*buffer.page
        if slot is None:
            start = time.monotonic_ns()
            buffer.mapping = mmap.mmap(-1, buffer.size, flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)
            buffer.mapping.madvise(mmap.MADV_NOHUGEPAGE)
            buffer.address = C.addressof(C.c_char.from_buffer(buffer.mapping))
            buffer.bind(node)
            slot = SimpleNamespace(mapping=buffer.mapping, address=buffer.address,
                                   size=buffer.size, signature=signature, busy=False)
            self.slots[identity] = slot
            self.new_bytes += buffer.size
            self.allocation_ns += time.monotonic_ns()-start
        else:
            if slot.busy or slot.signature != signature:
                raise RuntimeError('Cannot reuse live storage or changed saved-tensor signature')
            buffer.mapping, buffer.address = slot.mapping, slot.address
            self.reused_bytes += buffer.size
        owner = (C.c_char * buffer.size).from_buffer(buffer.mapping)
        slot.busy = True
        weakref.finalize(owner, self.release, slot)
        buffer.tensor = torch.frombuffer(owner, dtype=tensor.dtype, count=tensor.numel()).reshape(tensor.shape)
        buffer.tensor.copy_(tensor.detach())
        if is_new:
            self.materialize_ns += time.monotonic_ns() - acquisition_start
        if verify:
            buffer.query(node)
        return buffer

    def assert_idle(self):
        if any(slot.busy for slot in self.slots.values()):
            raise RuntimeError('Pool storage retained after graph/task completion')

    def close(self):
        self.assert_idle()
        for slot in self.slots.values():
            slot.mapping.close()
        self.closed = True
