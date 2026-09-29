#define _GNU_SOURCE
#include <errno.h>
#include <stdint.h>
#include <sys/resource.h>
#include <time.h>
#include <unistd.h>
#include <sys/syscall.h>

/* 仅测量既有 libnuma 调用；不改变节点、flags、页面列表或内核算法。 */
typedef long (*move_fn)(int, unsigned long, void **, const int *, int *, int);
struct sample {
    uint64_t start_ns, end_ns, cpu_ns;
    long voluntary, involuntary, tid;
};
static uint64_t ns(const struct timespec *t) {
    return (uint64_t)t->tv_sec * 1000000000ULL + t->tv_nsec;
}
long measured_move(move_fn fn, int pid, unsigned long count, void **pages,
                   const int *nodes, int *status, int flags, struct sample *out) {
    struct timespec c0,c1,w0,w1;
    struct rusage r0,r1;
    out->tid = syscall(SYS_gettid);
    if (getrusage(RUSAGE_THREAD,&r0) || clock_gettime(CLOCK_THREAD_CPUTIME_ID,&c0)
        || clock_gettime(CLOCK_MONOTONIC,&w0)) return -1;
    long rc=fn(pid,count,pages,nodes,status,flags);
    int saved_errno=errno;
    if (clock_gettime(CLOCK_MONOTONIC,&w1) || clock_gettime(CLOCK_THREAD_CPUTIME_ID,&c1)
        || getrusage(RUSAGE_THREAD,&r1)) return -1;
    out->start_ns=ns(&w0); out->end_ns=ns(&w1); out->cpu_ns=ns(&c1)-ns(&c0);
    out->voluntary=r1.ru_nvcsw-r0.ru_nvcsw;
    out->involuntary=r1.ru_nivcsw-r0.ru_nivcsw;
    errno=saved_errno;
    return rc;
}
