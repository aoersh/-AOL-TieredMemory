"""Isolated mmap tensors for placement/correctness pilots; Linux libnuma ABI."""
import ctypes as C
import mmap
import os
import time
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
