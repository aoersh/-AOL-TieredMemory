#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#define __USE_GNU
#include <stdio.h>
#include <stdint.h>
#include <pthread.h>
#include <assert.h>
#include <string.h>
#include <unistd.h>
#include <stdlib.h>
#include <dlfcn.h>
#include <sys/sysinfo.h>
#include <sys/time.h>
#include <execinfo.h>
#include <new>
#include <sys/types.h>
#include <numa.h>
#include <errno.h>

#define ARR_SIZE 550000            /* Max number of malloc per core */
#define MAX_TID 512                /* Max number of tids to profile */

#define USE_FRAME_POINTER   0      /* Use Frame Pointers to compute the stack trace (faster) */
#define CALLCHAIN_SIZE      5      /* stack trace length */
#define RESOLVE_SYMBS       1      /* Resolve symbols at the end of the execution; quite costly */

#define NB_ALLOC_TO_IGNORE   0     /* Ignore the first X allocations. */
#define IGNORE_FIRST_PROCESS 0     /* Ignore the first process (and all its threads). Useful for processes */

#define MAX_OBJECTS          30000

#if IGNORE_FIRST_PROCESS
static int first_pid;
#endif
#if NB_ALLOC_TO_IGNORE > 0
static int __thread nb_allocs = 0;
#endif

static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static int __thread pid;
static int __thread tid;
static int __thread tid_index;
static int tids[MAX_TID];

struct addr_seg {
    long unsigned start;
    long unsigned end;
    int node;
};

static struct addr_seg addr_segs[MAX_OBJECTS];
static size_t fast_limit = SIZE_MAX, fast_used, fast_peak;
static int fast_node = 0, slow_node = 1;
static bool budget_enabled;

static size_t mapped_size(size_t size)
{
    size_t page = (size_t)getpagesize();
    if (size > SIZE_MAX - page + 1) return 0;
    return (size + page - 1) & ~(page - 1);
}

struct log {
    uint64_t rdt;
    void *addr;
    size_t size;
    long entry_type; /* 0 free 1 malloc >=100 mmap */
    size_t callchain_size;
    void *callchain_strings[CALLCHAIN_SIZE];
};
struct log *log_arr[MAX_TID];
static size_t log_index[MAX_TID];

void __attribute__((constructor)) m_init(void);

#ifdef __x86_64__
#define rdtscll(val) { \
    unsigned int __a,__d;                                        \
    asm volatile("rdtsc" : "=a" (__a), "=d" (__d));              \
    (val) = ((unsigned long)__a) | (((unsigned long)__d)<<32);   \
}

#else
#define rdtscll(val) __asm__ __volatile__("rdtsc" : "=A" (val))
#endif

static int __thread _in_trace = 0;
#define get_bp(bp) asm("movq %%rbp, %0" : "=r" (bp) :)

static __attribute__((unused)) int in_first_dlsym = 0;
static char empty_data[32];
extern "C" void *__libc_calloc(size_t, size_t);

static void *(*libc_malloc)(size_t);
static void *(*libc_calloc)(size_t, size_t);
static void *(*libc_realloc)(void *, size_t);
static void (*libc_free)(void *);
static size_t (*libc_malloc_usable_size)(void *);

static void *(*libc_mmap)(void *, size_t, int, int, int, off_t);
static void *(*libc_mmap64)(void *, size_t, int, int, int, off_t);
static int (*libc_munmap)(void *, size_t);
static void *(*libc_memalign)(size_t, size_t);
static int (*libc_posix_memalign)(void **, size_t, size_t);

struct log *get_log()
{
    // The interceptor consumes each trace immediately; it has no log consumer.
    static __thread struct log current;
    memset(&current, 0, sizeof(current));
    return &current;
}

int get_trace(size_t *size, void **strings)
{
    if (_in_trace)
        return 1;
    _in_trace = 1;

#if USE_FRAME_POINTER
    int i;
    struct stack_frame *frame;
    get_bp(frame);
    for (i = 0; i < CALLCHAIN_SIZE; i++) {
        strings[i] = (void*)frame->return_address;
        *size = i + 1;
        frame = frame->next_frame;
        if (!frame)
            break;
    }
#else
    *size = backtrace(strings, CALLCHAIN_SIZE);
#endif
    _in_trace = 0;
    return 0;
}

int _getpid()
{
    if (pid)
        return pid;
    return pid = getpid();
}

int check_trace(void *string, size_t sz)
{
    char *ptr = (char *) string;
    // Optional exact call-site placement table: hexadecimal-address NUMA-node.
    // Keep the published policy below when no table is supplied.
    const char *policy = getenv("SOAR_POLICY");
    if (policy) {
        FILE *f = fopen(policy, "r");
        if (!f) { perror("SOAR_POLICY"); abort(); }
        unsigned long site, actual;
        int node, result = budget_enabled ? slow_node : -1;
        const char *address = strrchr(ptr, '[');
        if (address && sscanf(address, "[%lx]", &actual) == 1) {
            while (fscanf(f, "%lx %d", &site, &node) == 2) {
                if (site == actual) { result = node; break; }
            }
        }
        fclose(f);
        return result;
    }
    char *objs[] = {"405fb2", "406d68", "406fe7", "406d27", \
        "40b69c", "406cc3", "406db6", "40b62e"};
    int start = 0;
    int end = 8;
    int k1 = 7;
    int k2 = 8;
    for (int i = start; i < k1; i += 1) {
        if (strstr(ptr, objs[i]) != NULL) {
            return 0;
        }
    }
    for (int i = k1; i < k2; i += 1) {
        if (strstr(ptr, objs[i]) != NULL) {
            return -1;
        }
    }
    for (int i = k2; i < end; i += 1) {
        if (strstr(ptr, objs[i]) != NULL) {
            return 1;
        }
    }
    return -1;
}

void record_seg(unsigned long addr, size_t size, int node)
{
    pthread_mutex_lock(&lock);
    int i;
    for (i = 0; i < MAX_OBJECTS; i += 1) {
        struct addr_seg *seg = &addr_segs[i];
        if (seg->start == 0 && seg->end == 0) {
            seg->start = addr;
            seg->end = addr + size;
            seg->node = node;
            pthread_mutex_unlock(&lock);
            return;
        }
    }
    pthread_mutex_unlock(&lock);
    abort();
}

size_t check_seg(unsigned long addr, bool remove = true, int *node = NULL)
{
    pthread_mutex_lock(&lock);
    int i;
    size_t size_to_free;
    size_to_free = 0;
    for (i = 0; i < MAX_OBJECTS; i += 1) {
        struct addr_seg *seg = &addr_segs[i];
        if (seg->start == addr && seg->end > addr) {
            size_to_free = (size_t) (seg->end - addr);
            if (node) *node = seg->node;
            if (remove) {
                if (budget_enabled && seg->node == fast_node)
                    fast_used -= mapped_size(size_to_free);
                seg->start = 0;
                seg->end = 0;
            }
            break;
        }
    }
    pthread_mutex_unlock(&lock);
    return size_to_free;
}

static void *allocate_numa(size_t size, int *node)
{
    if (!numa_all_nodes_ptr || !numa_bitmask_isbitset(numa_all_nodes_ptr, *node)) {
        errno = EINVAL;
        return NULL;
    }
    size_t bytes = mapped_size(size);
    if (!bytes) { errno = ENOMEM; return NULL; }
    pthread_mutex_lock(&lock);
    if (budget_enabled && *node == fast_node) {
        if (bytes > fast_limit - fast_used) *node = slow_node;
        else fast_used += bytes;
    }
    pthread_mutex_unlock(&lock);
    void *address = numa_alloc_onnode(size, *node);
    pthread_mutex_lock(&lock);
    if (budget_enabled && *node == fast_node) {
        if (!address) fast_used -= bytes;
        else if (fast_used > fast_peak) fast_peak = fast_used;
    }
    pthread_mutex_unlock(&lock);
    if (address) record_seg((unsigned long)address, size, *node);
    return address;
}

extern "C" void *malloc(size_t sz)
{
    if (!libc_malloc)
        m_init();
    // libnuma allocates while constructing its own topology masks.
    if (!numa_all_nodes_ptr) return libc_malloc(sz);
    void *addr;
    struct log *log_arr = NULL;
    if (!_in_trace) {
        log_arr = get_log();
        if (log_arr) {
            rdtscll(log_arr->rdt);
            log_arr->size = sz;
            log_arr->entry_type = 1;
            get_trace(&log_arr->callchain_size, log_arr->callchain_strings);
        }
    }
    if (sz > 4096 && !_in_trace && log_arr) {
        if (log_arr->callchain_size >= 4) {
            _in_trace = 1;
            char **strings = backtrace_symbols (log_arr->callchain_strings, log_arr->callchain_size);
            int ret = check_trace(strings[3], sz);
            libc_free(strings);
            if (ret > -1) {
                addr = allocate_numa(sz, &ret);
                if (getenv("SOAR_PLACEMENT_LOG"))
                    fprintf(stderr, "SOAR_PLACE site=%p addr=%p bytes=%zu node=%d\n",
                            log_arr->callchain_strings[3], addr, sz, ret);
            } else {
                addr = libc_malloc(sz);
            }
            _in_trace = 0;
        } else {
            addr = libc_malloc(sz);
        }
    } else {
        addr = libc_malloc(sz);
    }
    return addr;
}

extern "C" void *calloc(size_t nmemb, size_t size)
{
    void *addr;
    if (!libc_calloc) {
        addr = __libc_calloc(nmemb, size);
    } else {
        addr = libc_calloc(nmemb, size);
    }
    if (!_in_trace && libc_calloc) {
        struct log *log_arr = get_log();
        if (log_arr) {
            rdtscll(log_arr->rdt);
            log_arr->addr = addr;
            log_arr->size = nmemb * size;
            log_arr->entry_type = 1;
            get_trace(&log_arr->callchain_size, log_arr->callchain_strings);
        }
    }
    return addr;
}

extern "C" void *realloc(void *ptr, size_t size)
{
    if (!libc_realloc) m_init();
    if (!ptr) return malloc(size);
    int node = -1;
    size_t old_size = check_seg((unsigned long)ptr, false, &node);
    if (old_size) {
        if (!size) { free(ptr); return NULL; }
        int tracing = _in_trace;
        _in_trace = 1;
        void *replacement = allocate_numa(size, &node);
        if (replacement) {
            memcpy(replacement, ptr, old_size < size ? old_size : size);
            check_seg((unsigned long)ptr);
            numa_free(ptr, old_size);
        }
        _in_trace = tracing;
        return replacement;
    }
    void *addr = libc_realloc(ptr, size);
    if (!_in_trace) {
        struct log *log_arr = get_log();
        if (log_arr) {
            rdtscll(log_arr->rdt);
            log_arr->addr = addr;
            log_arr->size = size;
            log_arr->entry_type = 1;
            get_trace(&log_arr->callchain_size, log_arr->callchain_strings);
        }
    }
    return addr;
}

extern "C" void *reallocarray(void *ptr, size_t nmemb, size_t size)
{
    if (size && nmemb > SIZE_MAX / size) { errno = ENOMEM; return NULL; }
    return realloc(ptr, nmemb * size);
}

extern "C" size_t malloc_usable_size(void *ptr)
{
    if (!ptr) return 0;
    size_t size = check_seg((unsigned long)ptr, false);
    if (size) return size;
    if (!libc_malloc_usable_size) m_init();
    return libc_malloc_usable_size(ptr);
}

extern "C" void *memalign(size_t align, size_t sz)
{
    void *addr = libc_memalign(align, sz);
    if (!_in_trace) {
        struct log *log_arr = get_log();
        if (log_arr) {
            rdtscll(log_arr->rdt);
            log_arr->addr = addr;
            log_arr->size = sz;
            log_arr->entry_type = 1;
            get_trace(&log_arr->callchain_size, log_arr->callchain_strings);
        }
    }
    return addr;
}

extern "C" int posix_memalign(void **ptr, size_t align, size_t sz)
{
    int ret = libc_posix_memalign(ptr, align, sz);
    if (!_in_trace && ret == 0) {
        struct log *log_arr = get_log();
        if (log_arr) {
            rdtscll(log_arr->rdt);
            log_arr->addr = *ptr;
            log_arr->size = sz;
            log_arr->entry_type = 1;
            get_trace(&log_arr->callchain_size, log_arr->callchain_strings);
        }
    }
    return ret;
}

extern "C" void free(void *p)
{
    if (!_in_trace && libc_free) {
        size_t size_to_free = check_seg((unsigned long) p);
        if (size_to_free > 0) {
            numa_free(p, size_to_free);
            return;
        } else {
            libc_free(p);
            return;
        }
    } else {
        libc_free(p);
    }
    /* libc_free(p); */
}

void *operator new(size_t sz) throw(std::bad_alloc)
{
    return malloc(sz);
}

void *operator new(size_t sz, const std::nothrow_t &) throw()
{
    return malloc(sz);
}

void *operator new[](size_t sz) throw(std::bad_alloc)
{
    return malloc(sz);
}

void *operator new[](size_t sz, const std::nothrow_t &) throw()
{
    return malloc(sz);
}

void operator delete(void *ptr)
{
    free(ptr);
}

void operator delete[](void *ptr)
{
    free(ptr);
}

extern "C" void *mmap(void *start, size_t length, int prot, int flags, int fd, off_t offset)
{
    void *addr = libc_mmap(start, length, prot, flags, fd, offset);

    if (!_in_trace) {
        struct log *log_arr = get_log();
        if (log_arr) {
            rdtscll(log_arr->rdt);
            log_arr->addr = addr;
            log_arr->size = length;
            log_arr->entry_type = flags + 100;
            get_trace(&log_arr->callchain_size, log_arr->callchain_strings);
        }
    }

    return addr;
}

extern "C" void *mmap64(void *start, size_t length, int prot, int flags, int fd, off_t offset)
{
    void *addr = libc_mmap64(start, length, prot, flags, fd, offset);

    if (!_in_trace) {
        struct log *log_arr = get_log();
        if (log_arr) {
            rdtscll(log_arr->rdt);
            log_arr->addr = addr;
            log_arr->size = length;
            log_arr->entry_type = flags + 100;
            get_trace(&log_arr->callchain_size, log_arr->callchain_strings);
        }
    }

    return addr;
}

extern "C" int munmap(void *start, size_t length)
{
    int addr = libc_munmap(start, length);
    struct log log_arr;
    rdtscll(log_arr.rdt);
    log_arr.addr = start;
    log_arr.size = length;
    log_arr.entry_type = 2;
    return addr;
}

int __thread bye_done = 0;
void __attribute__((destructor)) bye(void)
{
    if (bye_done)
        return;
    bye_done = 1;
    if (budget_enabled) {
        _in_trace = 1;
        fprintf(stderr, "SOAR_BUDGET limit=%zu peak=%zu live=%zu fast_node=%d slow_node=%d\n",
                fast_limit, fast_peak, fast_used, fast_node, slow_node);
    }
}

void __attribute__((constructor)) m_init(void)
{
    libc_malloc = (void * ( *)(size_t))dlsym(RTLD_NEXT, "malloc");
    libc_realloc = (void * ( *)(void *, size_t))dlsym(RTLD_NEXT, "realloc");
    libc_calloc = (void * ( *)(size_t, size_t))dlsym(RTLD_NEXT, "calloc");
    libc_free = (void ( *)(void *))dlsym(RTLD_NEXT, "free");
    libc_malloc_usable_size = (size_t (*)(void *))dlsym(RTLD_NEXT, "malloc_usable_size");
    libc_mmap = (void * ( *)(void *, size_t, int, int, int, off_t))dlsym(RTLD_NEXT, "mmap");
    libc_munmap = (int ( *)(void *, size_t))dlsym(RTLD_NEXT, "munmap");
    libc_mmap64 = (void * ( *)(void *, size_t, int, int, int, off_t))dlsym(RTLD_NEXT, "mmap64");
    libc_memalign = (void * ( *)(size_t, size_t))dlsym(RTLD_NEXT, "memalign");
    libc_posix_memalign = (int ( *)(void **, size_t, size_t))dlsym(RTLD_NEXT, "posix_memalign");
    const char *budget = getenv("SOAR_FAST_BYTES");
    if (budget) {
        char *end;
        errno = 0;
        unsigned long long value = strtoull(budget, &end, 10);
        if (errno || end == budget || *end || budget[0] == '-' || value > SIZE_MAX) abort();
        fast_limit = (size_t)value;
        if (getenv("SOAR_FAST_NODE")) fast_node = atoi(getenv("SOAR_FAST_NODE"));
        if (getenv("SOAR_SLOW_NODE")) slow_node = atoi(getenv("SOAR_SLOW_NODE"));
        if (fast_node < 0 || slow_node < 0 || fast_node == slow_node) abort();
        budget_enabled = true;
        numa_exit_on_error = 1;
    }
}
