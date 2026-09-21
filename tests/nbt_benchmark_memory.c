#define _GNU_SOURCE
#include <linux/mempolicy.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <unistd.h>

static void check(int ok, const char *message)
{
    if (!ok) { perror(message); exit(1); }
}

static int setting(const char *key, int max)
{
    const char *value = getenv(key);
    char *end;
    if (!value || !*value) { fprintf(stderr, "Missing %s\n", key); exit(2); }
    long n = strtol(value, &end, 10);
    if (*end || n < 0 || n > max) { fprintf(stderr, "Invalid %s\n", key); exit(2); }
    return n;
}

static void residency(void *ptr, size_t size, const char *phase, int expected)
{
    size_t page = (size_t)getpagesize(), count = size / page;
    void **pages = malloc(count * sizeof(*pages));
    int *status = malloc(count * sizeof(*status));
    unsigned long nodes[64] = {0};
    check(pages && status, "residency allocation");
    for (size_t i = 0; i < count; i++) pages[i] = (char *)ptr + i * page;
    check(syscall(SYS_move_pages, 0, count, pages, NULL, status, 0) == 0,
          "move_pages query");
    for (size_t i = 0; i < count; i++) {
        if (status[i] < 0 || status[i] >= 64 || (expected >= 0 && status[i] != expected)) {
            fprintf(stderr, "Invalid residency phase=%s status=%d expected=%d\n",
                    phase, status[i], expected);
            exit(1);
        }
        nodes[status[i]]++;
    }
    flockfile(stdout);
    printf("NBT_RESIDENCY {\"phase\":\"%s\",\"address\":\"%p\",\"pages\":%zu,\"nodes\":{",
           phase, ptr, count);
    int comma = 0;
    for (int i = 0; i < 64; i++) if (nodes[i]) {
        printf("%s\"%d\":%lu", comma ? "," : "", i, nodes[i]); comma = 1;
    }
    puts("}}");
    fflush(stdout);
    funlockfile(stdout);
    free(pages); free(status);
}

int init_buf_reg_alloc(uint64_t size, char **alloc_ptr)
{
    size_t page = (size_t)getpagesize();
    check(size > 0 && size % page == 0 && size <= SIZE_MAX - page, "buffer size");
    int node = setting("NBT_SOURCE_NODE", 63), keep = setting("NBT_KEEP_BIND", 1);
    unsigned long mask = 1UL << node;
    char *base = mmap(NULL, size + page, PROT_READ | PROT_WRITE,
                      MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    check(base != MAP_FAILED, "mmap");
    *(size_t *)base = size;
    char *ptr = base + page;
    check(madvise(ptr, size, MADV_NOHUGEPAGE) == 0, "madvise");
    check(syscall(SYS_mbind, ptr, size, MPOL_BIND, &mask, 64, 0) == 0, "mbind");
    for (size_t offset = 0; offset < size; offset += page) ptr[offset] = 0;
    residency(ptr, size, "initial", node);
    if (!keep)
        check(syscall(SYS_mbind, ptr, size, MPOL_DEFAULT, NULL, 0, 0) == 0,
              "clear binding");
    *alloc_ptr = ptr;
    return 0;
}

void aligned_free(void *ptr)
{
    size_t page = (size_t)getpagesize();
    char *base = (char *)ptr - page;
    size_t size = *(size_t *)base;
    residency(ptr, size, "final", setting("NBT_KEEP_BIND", 1) ?
              setting("NBT_SOURCE_NODE", 63) : -1);
    check(munmap(base, size + page) == 0, "munmap");
}
