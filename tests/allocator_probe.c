#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <malloc.h>

__attribute__((noinline)) static void *allocate(size_t n) { return malloc(n); }

__attribute__((noinline)) static void cycle(void)
{
    unsigned char *p = allocate(8192);
    assert(p);
    memset(p, 0x5a, 8192);
    assert(malloc_usable_size(p) >= 8192);
    unsigned char *q = realloc(p, 32768);
    assert(q);
    for (int i = 0; i < 8192; i++) assert(q[i] == 0x5a);
    p = realloc(q, 4096);
    assert(p);
    for (int i = 0; i < 4096; i++) assert(p[i] == 0x5a);
    volatile size_t huge = SIZE_MAX;
    assert(!reallocarray(p, huge, 2));
    q = realloc(p, huge);
    assert(!q);
    for (int i = 0; i < 4096; i++) assert(p[i] == 0x5a);
    free(p);
    p = calloc(8192, 1);
    assert(p);
    for (int i = 0; i < 8192; i++) assert(p[i] == 0);
    q = realloc(p, 16384);
    assert(q);
    free(q);
    void *sentinel = (void *)123;
    assert(posix_memalign(&sentinel, 3, 8192) == EINVAL);
    assert(sentinel == (void *)123);
    assert(!calloc(huge, 2));
    assert(posix_memalign(&sentinel, 4096, 8192) == 0);
    memset(sentinel, 0, 8192);
    free(sentinel);
    p = realloc(NULL, 8192);
    assert(p);
    assert(realloc(p, 0) == NULL);
    free(NULL);
}

static void *worker(void *unused)
{
    (void)unused;
    for (int i = 0; i < 20; i++) cycle();
    return NULL;
}

int main(void)
{
    pthread_t threads[4];
    cycle();
    for (int i = 0; i < 4; i++) assert(!pthread_create(&threads[i], NULL, worker, NULL));
    for (int i = 0; i < 4; i++) assert(!pthread_join(threads[i], NULL));
    puts("allocator semantics passed");
    return 0;
}
