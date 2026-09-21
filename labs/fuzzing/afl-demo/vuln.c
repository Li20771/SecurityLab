/*
 * SecurityLab AFL++ teaching target - SYNTHETIC, intentionally vulnerable.
 *
 * There is no real-world vulnerability here. The only "parser" is the one
 * deliberately written for this exercise so AFL++ can discover, save and
 * deterministically reproduce a crash within seconds.
 *
 * Made-up protocol:
 *   bytes [0..2] = magic "AFL"
 *   byte  [3]    = payload length L (attacker controlled)
 *   bytes [4..]  = payload
 * buggy_copy() copies L bytes into a 16-byte stack buffer with no bounds
 * check. Under ASan this is a stack-buffer-overflow (SIGABRT); built
 * -fno-stack-protector without ASan it overwrites the saved return address
 * (SIGSEGV on return).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

__attribute__((noinline))
void buggy_copy(const unsigned char *in, size_t n)
{
    unsigned char buf[16];                /* fixed 16-byte stack buffer */
    size_t len = in[3];                   /* attacker controlled, unchecked */
    if (len > n - 4) len = n - 4;
    memcpy(buf, in + 4, len);             /* BUG: stack overflow if len > 16 */
    printf("processed %zu bytes\n", len);
}

int main(int argc, char **argv)
{
    if (argc < 2) {
        fprintf(stderr, "usage: %s <input-file>\n", argv[0]);
        return 2;
    }
    FILE *f = fopen(argv[1], "rb");
    if (!f) {
        perror("fopen");
        return 2;
    }
    unsigned char in[256];
    size_t n = fread(in, 1, sizeof(in), f);
    fclose(f);

    if (n >= 4 && in[0] == 'A' && in[1] == 'F' && in[2] == 'L') {
        buggy_copy(in, n);
    }
    return 0;
}
