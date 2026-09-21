#define _GNU_SOURCE
#include <errno.h>
#include <linux/mempolicy.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

static void check(int ok, const char *operation)
{
    if (!ok) { perror(operation); exit(1); }
}

static double now(void)
{
    struct timespec t;
    check(clock_gettime(CLOCK_MONOTONIC, &t) == 0, "clock_gettime");
    return t.tv_sec + t.tv_nsec / 1e9;
}

static int number(const char *s, int low, int high)
{
    char *end;
    errno = 0;
    long n = strtol(s, &end, 10);
    if (errno || !*s || *end || n < low || n > high) {
        fprintf(stderr, "Invalid argument: %s\n", s); exit(2);
    }
    return (int)n;
}

static void snapshot(void **pages, int *status, size_t count, double elapsed)
{
    unsigned long nodes[64] = {0};
    unsigned long errors = 0;
    check(syscall(SYS_move_pages, 0, count, pages, NULL, status, 0) == 0,
          "move_pages query");
    for (size_t i = 0; i < count; i++) {
        if (status[i] >= 0 && status[i] < 64) nodes[status[i]]++;
        else errors++;
    }
    printf("{\"seconds\":%.6f,\"pages\":%zu,\"errors\":%lu,\"nodes\":{",
           elapsed, count, errors);
    int comma = 0;
    for (int i = 0; i < 64; i++) if (nodes[i]) {
        printf("%s\"%d\":%lu", comma ? "," : "", i, nodes[i]); comma = 1;
    }
    puts("}}");
    fflush(stdout);
    if (errors) { fprintf(stderr, "Page residency query errors\n"); exit(1); }
}

int main(int argc, char **argv)
{
    if (argc != 6) {
        fprintf(stderr, "Usage: %s SOURCE_NODE CPU KEEP_BIND SECONDS MIB\n", argv[0]);
        return 2;
    }
    int node = number(argv[1], 0, 63), cpu = number(argv[2], 0, CPU_SETSIZE - 1);
    int keep = number(argv[3], 0, 1), duration = number(argv[4], 1, 300);
    size_t bytes = (size_t)number(argv[5], 1, 1024) << 20;
    long page_size = sysconf(_SC_PAGESIZE);
    check(page_size > 0, "page_size");
    size_t count = bytes / (size_t)page_size;
    unsigned long mask = 1UL << node;
    cpu_set_t cpus;
    CPU_ZERO(&cpus); CPU_SET(cpu, &cpus);
    check(sched_setaffinity(0, sizeof(cpus), &cpus) == 0, "affinity");
    volatile unsigned char *memory = mmap(NULL, bytes, PROT_READ | PROT_WRITE,
                                         MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    check(memory != MAP_FAILED, "mmap");
    check(madvise((void *)memory, bytes, MADV_NOHUGEPAGE) == 0, "nohugepage");
    check(syscall(SYS_mbind, memory, bytes, MPOL_BIND, &mask, 64, 0) == 0, "mbind");
    void **pages = malloc(count * sizeof(*pages));
    int *status = malloc(count * sizeof(*status));
    check(pages && status, "malloc");
    for (size_t i = 0; i < count; i++) {
        memory[i * page_size] = 1;
        pages[i] = (void *)(memory + i * page_size);
    }
    snapshot(pages, status, count, 0);
    for (size_t i = 0; i < count; i++) if (status[i] != node) {
        fprintf(stderr, "Initial placement differs from requested node\n"); return 1;
    }
    if (!keep)
        check(syscall(SYS_mbind, memory, bytes, MPOL_DEFAULT, NULL, 0, 0) == 0,
              "clear binding");
    double start = now(), next = 1;
    uint64_t checksum = 0;
    while (now() - start < duration) {
        for (size_t i = 0; i < count; i++) checksum += memory[i * page_size];
        double elapsed = now() - start;
        if (elapsed >= next) { snapshot(pages, status, count, elapsed); next += 1; }
    }
    snapshot(pages, status, count, now() - start);
    fprintf(stderr, "checksum=%llu\n", (unsigned long long)checksum);
    free(pages); free(status);
    check(munmap((void *)memory, bytes) == 0, "munmap");
    return 0;
}
