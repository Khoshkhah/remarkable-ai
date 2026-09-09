/* common.h: what rmdash.c and rmvocab.c share beyond the pen (stylus.h): HTTPS through the tablet's
 * openssl, a small JSON scanner, CRC32, and the deflate of our own (no zlib on the tablet). */
static int https_timeout = 25;   /* seconds openssl gets for one request; rmvocab raises it for Gemini */

static int https(const char *host, const char *request, char *out, size_t cap) {
    const char *fx = getenv("RM_FIXTURES");   /* dry runs off the tablet: canned responses */
    if (fx) {
        char p[600]; snprintf(p, sizeof p, "%s/%s", fx, strstr(host, "meteo") ? "weather.json" : strstr(host, "console") ? "token.json" : strstr(host, "google") ? "gemini.json" : "usage.json");
        FILE *f = fopen(p, "r"); if (!f) return -1;
        size_t n = fread(out, 1, cap - 1, f); out[n] = 0; fclose(f); return 200;
    }
    char req[600]; snprintf(req, sizeof req, "%s/req", dir);
    FILE *f = fopen(req, "w"); if (!f) return -1;
    chmod(req, 0600); fputs(request, f); fclose(f);
    char cmd[1200];
    snprintf(cmd, sizeof cmd, "openssl s_client -quiet -ign_eof -connect %s:443 -servername %s -verify_return_error -CApath /etc/ssl/certs < %s 2>/dev/null & p=$!; "
             "(sleep %d; kill $p 2>/dev/null) >/dev/null 2>&1 & w=$!; wait $p; kill $w 2>/dev/null", host, host, req, https_timeout);
    FILE *pp = popen(cmd, "r"); if (!pp) return -1;
    size_t n = fread(out, 1, cap - 1, pp); out[n] = 0; pclose(pp); unlink(req);
    if (n < 12 || strncmp(out, "HTTP/", 5)) return -1;
    int status = atoi(out + 9);
    char *body = strstr(out, "\r\n\r\n");
    if (!body) return -1;
    memmove(out, body + 4, strlen(body + 4) + 1);
    return status;
}

static const char *jkey(const char *s, const char *key) {   /* after `"key":` and any blanks (first occurrence from s) */
    char k[64]; snprintf(k, sizeof k, "\"%s\":", key);
    const char *p = strstr(s, k); if (!p) return NULL;
    p += strlen(k); while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r') p++;
    return p;
}
static double jnum(const char *s, const char *key, double dflt) { const char *p = jkey(s, key); return p && (*p == '-' || (*p >= '0' && *p <= '9')) ? atof(p) : dflt; }
static int jstr(const char *s, const char *key, char *out, size_t cap) {
    const char *p = jkey(s, key); if (!p || *p != '"') return 0;
    p++; size_t i = 0; while (*p && *p != '"' && i < cap - 1) out[i++] = *p++; out[i] = 0; return 1;
}
static int jarr(const char *s, const char *key, double *out, int max) {   /* numbers (null -> -999) */
    const char *p = jkey(s, key); if (!p || *p != '[') return 0;
    int n = 0; p++;
    while (*p && *p != ']' && n < max) {
        while (*p == ' ' || *p == ',') p++;
        if (*p == '"') { p++; while (*p && *p != '"') p++; if (*p) p++; out[n++] = 0; continue; }
        out[n++] = (*p == 'n') ? -999 : atof(p);
        while (*p && *p != ',' && *p != ']') p++;
    }
    return n;
}
static int jarr_str(const char *s, const char *key, char out[][12], int max) {   /* keeps the first 11 chars, e.g. a date or "2026-09-09T06:41" -> the time part is what matters */
    const char *p = jkey(s, key); if (!p || *p != '[') return 0;
    int n = 0; p++;
    while (*p && *p != ']' && n < max) {
        while (*p == ' ' || *p == ',') p++;
        if (*p != '"') break;
        p++; const char *q = p; while (*q && *q != '"') q++;
        const char *t = memchr(p, 'T', q - p);                   /* an ISO time: keep HH:MM only */
        if (t && q - t >= 6) snprintf(out[n], 12, "%.5s", t + 1); else snprintf(out[n], 12, "%.*s", (int)(q - p) > 11 ? 11 : (int)(q - p), p);
        p = *q ? q + 1 : q; n++;
    }
    return n;
}

static uint32_t crc_table[256];
static uint32_t crc32_(uint32_t c, const unsigned char *b, size_t n) {
    if (!crc_table[1]) for (uint32_t i = 0; i < 256; i++) { uint32_t r = i; for (int k = 0; k < 8; k++) r = r & 1 ? 0xEDB88320u ^ (r >> 1) : r >> 1; crc_table[i] = r; }
    c ^= 0xFFFFFFFFu; for (size_t i = 0; i < n; i++) c = crc_table[(c ^ b[i]) & 255] ^ (c >> 8); return c ^ 0xFFFFFFFFu;
}
static void be32(unsigned char *p, uint32_t v) { p[0] = v >> 24; p[1] = v >> 16; p[2] = v >> 8; p[3] = v; }
static struct { unsigned char *buf; size_t n, cap; uint32_t acc; int nb; } bw;
static void put_bits(uint32_t v, int n) {   /* LSB first, as deflate wants */
    bw.acc |= v << bw.nb; bw.nb += n;
    while (bw.nb >= 8) { if (bw.n < bw.cap) bw.buf[bw.n++] = bw.acc & 255; bw.acc >>= 8; bw.nb -= 8; }
}
static void put_huff(uint32_t code, int n) {   /* Huffman codes go MSB first */
    uint32_t r = 0; for (int i = 0; i < n; i++) r = (r << 1) | ((code >> i) & 1);
    put_bits(r, n);
}
static void put_literal(int b) { if (b < 144) put_huff(0x30 + b, 8); else put_huff(0x190 + b - 144, 9); }
static void put_run(int len) {   /* a <length, distance 1> match: 3..258 */
    static const int base[] = {3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258};
    static const int extra[] = {0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0};
    int i = 28; while (base[i] > len) i--;
    int sym = 257 + i;
    if (sym < 280) put_huff(sym - 256, 7); else put_huff(0xC0 + sym - 280, 8);
    if (extra[i]) put_bits(len - base[i], extra[i]);
    put_huff(0, 5);   /* distance code 0 = distance 1 */
}
static size_t deflate_runs(const unsigned char *raw, size_t n, unsigned char *out, size_t cap) {
    bw.buf = out; bw.n = 0; bw.cap = cap; bw.acc = 0; bw.nb = 0;
    put_bits(1, 1); put_bits(1, 2);   /* final block, fixed Huffman */
    size_t i = 0;
    while (i < n) {
        put_literal(raw[i]);
        size_t j = i + 1; while (j < n && raw[j] == raw[i] && j - i - 1 < 258) j++;
        size_t run = j - i - 1;
        if (run >= 3) { put_run((int)run); i = j; } else i++;
    }
    put_huff(0, 7);   /* end of block */
    if (bw.nb) put_bits(0, 8 - bw.nb);
    return bw.n;
}
/* a zlib stream (what PNG's IDAT and PDF's FlateDecode both want) of `raw`, into `out`; returns its length */
static size_t zlib_stream(const unsigned char *raw, size_t n, unsigned char *out, size_t cap) {
    out[0] = 0x78; out[1] = 0x01;
    size_t p = 2 + deflate_runs(raw, n, out + 2, cap - 8);
    uint32_t a = 1, b = 0;
    for (size_t i = 0; i < n; i++) { a = (a + raw[i]) % 65521; b = (b + a) % 65521; }
    be32(out + p, (b << 16) | a);
    return p + 4;
}
