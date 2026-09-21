#define _GNU_SOURCE
#include <assert.h>
#include <linux/mempolicy.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

static double seconds(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}

int main(void)
{
    const size_t bytes = 64UL << 20;
    unsigned long mask = 2;
    cpu_set_t cpus;
    CPU_ZERO(&cpus);
    CPU_SET(0, &cpus);
    assert(sched_setaffinity(0, sizeof(cpus), &cpus) == 0);
    volatile unsigned char *memory = mmap(NULL, bytes, PROT_READ | PROT_WRITE,
                                         MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    assert(memory != MAP_FAILED);
    assert(syscall(SYS_mbind, memory, bytes, MPOL_BIND, &mask, 64, 0) == 0);
    for (size_t offset = 0; offset < bytes; offset += 4096) memory[offset] = 1;
    assert(syscall(SYS_mbind, memory, bytes, MPOL_DEFAULT, NULL, 0, 0) == 0);
    double end = seconds() + 8;
    uint64_t checksum = 0;
    while (seconds() < end)
        for (size_t offset = 0; offset < bytes; offset += 4096) checksum += memory[offset];
    void *pages[16];
    int status[16];
    for (int i = 0; i < 16; i++) pages[i] = (void *)(memory + i * (bytes / 16));
    assert(syscall(SYS_move_pages, 0, 16, pages, NULL, status, 0) == 0);
    int local = 0;
    for (int i = 0; i < 16; i++) if (status[i] == 0) local++;
    printf("PROBE local_samples=%d total=16 checksum=%lu\n", local, checksum);
    munmap((void *)memory, bytes);
    return 0;
}
