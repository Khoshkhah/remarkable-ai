/* Writes a .rm v6 page so the writer can be checked against rmscene on the PC:
 *   gcc -o /tmp/w app/test_rmwrite.c && /tmp/w /tmp/out.rm     */
#include "rmwrite.h"

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: %s <out.rm>\n", argv[0]); return 1; }
    unsigned char author[16] = {0x52,0xb6,0x64,0xc0,0x6f,0x37,0x2d,0x43,0x8b,0x67,0x3a,0x30,0x97,0x76,0x06,0x95};
    struct rmbuf out = {0};
    rb_bytes(&out, RM_HEADER, 43);
    rm_page_head(&out, author, 1);
    rm_layer(&out, 21, 22, "Words", 1, 23, 0);
    rm_layer(&out, 31, 32, "holdout", 1, 33, 23);
    /* a frame on the second layer, plus one diagonal so the points are easy to check */
    struct rmpt frame[5] = {{140,260},{1260,260},{1260,690},{140,690},{140,260}};
    rm_line(&out, 31, 34, 0, frame, 5, RM_PEN_MARKER, 3.0f);
    struct rmpt diag[2] = {{200,320},{600,640}};
    rm_line(&out, 31, 35, 34, diag, 2, RM_PEN_FINELINER, 2.0f);
    FILE *f = fopen(argv[1], "wb");
    if (!f) { perror(argv[1]); return 1; }
    fwrite(out.p, 1, out.n, f); fclose(f);
    printf("wrote %zu bytes\n", out.n);

    struct rmscan s;
    if (!rm_scan(argv[1], &s)) { fprintf(stderr, "scan failed\n"); return 1; }
    printf("scan: max_id=%llu last_child=%llu layers=%d\n",
           (unsigned long long)s.max_id, (unsigned long long)s.last_child, s.layers);
    return 0;
}
