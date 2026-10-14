"""C10 tensor-storage allocation quota and diagnostic-only page audit."""
import ctypes as C
import json
from pathlib import Path

FIELDS = ['limit', 'policy', 'fast_live', 'slow_live', 'fast_peak', 'slow_peak',
          'total_peak', 'allocations', 'frees', 'failures', 'allocated_bytes', 'live_records']


class TensorCapacity:
    def __init__(self, directory, limit, policy):
        self.directory = Path(directory)
        self.library = C.CDLL(str((self.directory/'libtensor_capacity.so').resolve()))
        self.library.tensor_capacity_install.argtypes = [C.c_uint64, C.c_uint64]
        self.library.tensor_capacity_install.restype = C.c_int
        self.library.tensor_capacity_stats.argtypes = [C.POINTER(C.c_uint64)]
        self.library.tensor_capacity_stats.restype = None
        self.library.tensor_capacity_records.argtypes = [C.POINTER(C.c_uint64), C.c_uint64]
        self.library.tensor_capacity_records.restype = C.c_uint64
        self.library.tensor_capacity_lookup.argtypes = [C.c_void_p]
        self.library.tensor_capacity_lookup.restype = C.c_int
        assert self.library.tensor_capacity_install(limit, policy) == 0

    def stats(self):
        values = (C.c_uint64*len(FIELDS))()
        self.library.tensor_capacity_stats(values)
        return dict(zip(FIELDS, values))

    def lookup(self, tensor):
        return self.library.tensor_capacity_lookup(tensor.untyped_storage().data_ptr())

    def audit(self):
        from numa_buffer import lib, checked
        capacity = self.library.tensor_capacity_records(None, 0)
        values = (C.c_uint64*(capacity*4))()
        count = self.library.tensor_capacity_records(values, capacity)
        assert count == capacity
        records = []
        resident = {'0': 0, '2': 0}
        reserved = {'0': 0, '2': 0}
        for index in range(count):
            address, size, logical, node = values[index*4:index*4+4]
            pages = (C.c_void_p*(size//4096))(*(address+i*4096 for i in range(size//4096)))
            status = (C.c_int*len(pages))()
            checked(lib.move_pages(0, len(pages), pages, None, status, 0))
            materialized = 0
            if -14 in status:
                memory = C.CDLL(None, use_errno=True)
                memory.mincore.argtypes = [C.c_void_p, C.c_size_t, C.POINTER(C.c_ubyte)]
                memory.mincore.restype = C.c_int
                present = (C.c_ubyte*len(pages))()
                checked(memory.mincore(address, size, present))
                # Query may reject a shared zero page; materialize only in diagnostics.
                for page, actual in zip(pages, status):
                    if actual == -14:
                        byte = C.c_ubyte.from_address(page)
                        byte.value = byte.value
                        materialized += 1
                checked(lib.move_pages(0, len(pages), pages, None, status, 0))
            counts = {}
            for actual in status:
                assert actual in (node, -2), (node, actual)
                key = str(actual)
                counts[key] = counts.get(key, 0)+1
            resident[str(node)] += counts.get(str(node), 0)*4096
            reserved[str(node)] += size
            records.append(dict(address=address, bytes=size, logical=logical, node=node,
                                pages=counts, diagnostic_materialized_pages=materialized))
        stats = self.stats()
        assert reserved == {'0': stats['fast_live'], '2': stats['slow_live']}
        return dict(stats=stats, reserved=reserved, resident=resident, records=records)
