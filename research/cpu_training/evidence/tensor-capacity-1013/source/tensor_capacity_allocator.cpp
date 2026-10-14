// Process-local C10 CPU allocator: page reservations bound tensor storage only.
#include <c10/core/CPUAllocator.h>
#include <sys/mman.h>
#include <numaif.h>
#include <cerrno>
#include <cstring>
#include <limits>
#include <mutex>
#include <new>
#include <unordered_map>

struct Record { size_t bytes, logical; int node; };
struct State {
    std::mutex mutex;
    std::unordered_map<void*, Record> records;
    uint64_t limit = 0, policy = 0, fast = 0, slow = 0;
    uint64_t fast_peak = 0, slow_peak = 0, total_peak = 0;
    uint64_t allocations = 0, frees = 0, failures = 0, allocated_bytes = 0;
};

static State& state() {
    static State* value = new State;
    return *value;
}

static void release(void* address) {
    if (!address) return;
    auto& s = state();
    std::lock_guard<std::mutex> guard(s.mutex);
    auto found = s.records.find(address);
    if (found == s.records.end()) std::abort();
    const auto record = found->second;
    // Refund after unmap, so a concurrent allocation cannot exceed the quota.
    if (munmap(address, record.bytes)) std::abort();
    (record.node == 0 ? s.fast : s.slow) -= record.bytes;
    ++s.frees;
    s.records.erase(found);
}

class TensorAllocator final : public c10::Allocator {
public:
    c10::DataPtr allocate(size_t logical) override {
        if (!logical) return {nullptr, c10::Device(c10::DeviceType::CPU)};
        if (logical > std::numeric_limits<size_t>::max()-4095) throw std::bad_alloc();
        const size_t bytes = (logical+4095) & ~size_t(4095);
        auto& s = state();
        std::lock_guard<std::mutex> guard(s.mutex);
        bool admit = s.policy == 1 || (s.policy >= 3 && bytes <= s.limit-s.fast);
        if (s.policy == 4 && logical > 1048576) admit = false;
        const int node = admit ? 0 : 2;
        void* address = mmap(nullptr, bytes, PROT_READ | PROT_WRITE,
                             MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (address == MAP_FAILED) { ++s.failures; throw std::bad_alloc(); }
        unsigned long mask = 1UL << node;
        if (madvise(address, bytes, MADV_NOHUGEPAGE) ||
            mbind(address, bytes, MPOL_BIND, &mask, 64, 0)) {
            munmap(address, bytes);
            ++s.failures;
            throw std::bad_alloc();
        }
        try {
            s.records.emplace(address, Record{bytes, logical, node});
        } catch (...) {
            munmap(address, bytes);
            ++s.failures;
            throw;
        }
        (node == 0 ? s.fast : s.slow) += bytes;
        s.fast_peak = std::max(s.fast_peak, s.fast);
        s.slow_peak = std::max(s.slow_peak, s.slow);
        s.total_peak = std::max(s.total_peak, s.fast+s.slow);
        ++s.allocations;
        s.allocated_bytes += bytes;
        return {address, address, release, c10::Device(c10::DeviceType::CPU)};
    }
    c10::DeleterFnPtr raw_deleter() const override { return release; }
    void copy_data(void* destination, const void* source, size_t bytes) const override {
        std::memcpy(destination, source, bytes);
    }
};

extern "C" int tensor_capacity_install(uint64_t limit, uint64_t policy) {
    auto& s = state();
    if (s.policy || policy < 1 || policy > 4) return -1;
    s.limit = limit;
    s.policy = policy;
    c10::SetCPUAllocator(new TensorAllocator, 255);
    return 0;
}

extern "C" void tensor_capacity_stats(uint64_t* out) {
    auto& s = state();
    std::lock_guard<std::mutex> guard(s.mutex);
    uint64_t values[] = {s.limit, s.policy, s.fast, s.slow, s.fast_peak, s.slow_peak,
                         s.total_peak, s.allocations, s.frees, s.failures,
                         s.allocated_bytes, s.records.size()};
    std::memcpy(out, values, sizeof(values));
}

extern "C" uint64_t tensor_capacity_records(uint64_t* out, uint64_t capacity) {
    auto& s = state();
    std::lock_guard<std::mutex> guard(s.mutex);
    if (capacity < s.records.size()) return s.records.size();
    size_t index = 0;
    for (const auto& item : s.records) {
        out[index++] = reinterpret_cast<uintptr_t>(item.first);
        out[index++] = item.second.bytes;
        out[index++] = item.second.logical;
        out[index++] = item.second.node;
    }
    return s.records.size();
}

extern "C" int tensor_capacity_lookup(void* address) {
    auto& s = state();
    std::lock_guard<std::mutex> guard(s.mutex);
    return s.records.count(address) ? s.records.at(address).node : -1;
}
