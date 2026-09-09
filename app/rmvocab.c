/* rmvocab: English lessons made by the reMarkable itself.
 *
 * The learner writes a word (and a sentence with it) on the Words page in the app folder and draws a
 * loop around the word. This program watches that page's file: the circled handwriting and the whole
 * page go to Gemini as two small PNGs with the tablet's own key, the lesson comes back as text, and
 * the program prints it onto page images with the glyph atlases the installer baked (Farsi letters
 * joined and lines laid right-to-left here, no shaping library on board). While nothing is open on
 * the tablet it rebuilds the Vocabulary document from every lesson page, clears the Words page and
 * restarts xochitl; a check mark drawn next to the loop says the lesson is made.
 * Next to the program: config (doc= pdf= wdir= words= [models=]), key, teacher.md, f<size><b|r>.atlas
 * (ATLU: Unicode keys with the Arabic form in bits 21+), check.bin, lessons/NNNN.txt + NNNN-<p>.z (one
 * zlib stream per printed page), inbox/<name>.txt (phrase, context, source: lookups sent by the PC), done
 * (loops already turned into lessons on the current Words page), state (pushed=).
 * `rmvocab <dir> [xochitl.conf] [event device]`; RM_FIXTURES=<dir> answers Gemini from gemini.json.
 */
#include "stylus.h"
#include "common.h"
#include <dirent.h>

#define PW 1404
#define PH 1872
#define LEFT 80
#define RIGHT 1324
#define TOP 90
#define BOTTOM 1790
#define PTS (72.0 / 226)
#define MAXCP 4096
#define RESP_CAP (1 << 19)
#define RETRY_S 60

static char doc[64], pdfpath[600], wdir[600], words_doc[64], key[256];
static char models[400] = "gemini-flash-latest,gemini-flash-lite-latest,gemini-3.5-flash";
static char lessons_dir[600], inbox_dir[600], done_path[600];
static char resp[RESP_CAP];

static uint32_t u32(const unsigned char *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }
static float f32(const unsigned char *p) { float f; memcpy(&f, p, 4); return f; }
static char *slurp(const char *path, size_t *len) {
    FILE *f = fopen(path, "rb"); if (!f) { if (len) *len = 0; return NULL; }
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    char *b = malloc(n + 1); size_t r = fread(b, 1, n, f); fclose(f); b[r] = 0;
    if (len) *len = r;
    return b;
}

/* ---------- the page file: strokes with their points (.rm v6, SceneLineItem blocks) ---------- */
struct pt { float x, y; };
struct stroke { struct pt *p; int n, tool; float x0, y0, x1, y1; };
static struct stroke *strokes; static int nstrokes, capstrokes;

static void free_strokes(void) { for (int i = 0; i < nstrokes; i++) free(strokes[i].p); nstrokes = 0; }
static int tagged(const unsigned char **b, const unsigned char *e, unsigned char tag) { if (*b < e && **b == tag) { (*b)++; return 1; } return 0; }
static int read_page(const char *path) {   /* strokes on the page (display px), -1 if unreadable */
    free_strokes();
    size_t n; unsigned char *buf = (unsigned char *)slurp(path, &n);
    if (!buf) return -1;
    size_t p = 43;
    while (p + 8 <= n) {
        uint32_t len = u32(buf + p); int ver = buf[p + 6], type = buf[p + 7];
        const unsigned char *b = buf + p + 8, *e = b + len;
        if (e > buf + n) break;
        p += 8 + len;
        if (type != 5) continue;
        if (!(rm_skip_tag(&b, e, 0x1F, 0) && rm_skip_tag(&b, e, 0x2F, 0) && rm_skip_tag(&b, e, 0x3F, 0) && rm_skip_tag(&b, e, 0x4F, 0) && rm_skip_tag(&b, e, 0x54, 1))) continue;
        if (!tagged(&b, e, 0x6C) || b + 5 > e) continue;   /* the item: a subblock ... */
        b += 4;
        if (*b++ != 3) continue;                              /* ... holding a line */
        int tool = -1;
        if (tagged(&b, e, 0x14) && b + 4 <= e) { tool = (int)u32(b); b += 4; }
        if (tagged(&b, e, 0x24)) b += 4;
        if (tagged(&b, e, 0x38)) b += 8;
        if (tagged(&b, e, 0x44)) b += 4;
        if (!tagged(&b, e, 0x5C) || b + 4 > e) continue;
        uint32_t plen = u32(b); b += 4;
        int psz = ver == 1 ? 24 : 14, np = (int)(plen / psz);
        if (b + plen > e || np < 1) continue;
        if (nstrokes == capstrokes) { capstrokes = capstrokes ? capstrokes * 2 : 256; strokes = realloc(strokes, capstrokes * sizeof *strokes); }
        struct stroke *s = &strokes[nstrokes++];
        s->p = malloc(np * sizeof *s->p); s->n = np; s->tool = tool;
        s->x0 = s->y0 = 1e9f; s->x1 = s->y1 = -1e9f;
        for (int i = 0; i < np; i++) {
            s->p[i].x = f32(b + i * psz) + 702; s->p[i].y = f32(b + i * psz + 4);
            if (s->p[i].x < s->x0) s->x0 = s->p[i].x;
            if (s->p[i].x > s->x1) s->x1 = s->p[i].x;
            if (s->p[i].y < s->y0) s->y0 = s->p[i].y;
            if (s->p[i].y > s->y1) s->y1 = s->p[i].y;
        }
    }
    free(buf);
    return nstrokes;
}
static int is_ink(int tool) { return tool != 5 && tool != 18 && tool != 6 && tool != 8; }   /* not a highlighter, not an eraser */
static int is_loop(const struct stroke *s) {   /* is_box() in rm_ai.py: one closed lap around something */
    float w = s->x1 - s->x0, h = s->y1 - s->y0;
    if (w < 80 || h < 40 || s->n < 8) return 0;
    if (hypotf(s->p[0].x - s->p[s->n - 1].x, s->p[0].y - s->p[s->n - 1].y) > fmaxf(40, 0.2f * hypotf(w, h))) return 0;
    double len = 0; for (int i = 1; i < s->n; i++) len += hypotf(s->p[i].x - s->p[i - 1].x, s->p[i].y - s->p[i - 1].y);
    double r = len / (2 * (w + h));
    return r >= 0.6 && r <= 1.6;
}
static int inside(float x, float y, const struct stroke *loop) {   /* ray casting */
    int hit = 0;
    for (int i = 0, j = loop->n - 1; i < loop->n; j = i++) {
        float ax = loop->p[i].x, ay = loop->p[i].y, bx = loop->p[j].x, by = loop->p[j].y;
        if ((ay > y) != (by > y) && x < ax + (y - ay) * (bx - ax) / (by - ay)) hit = !hit;
    }
    return hit;
}
static int content_of(const struct stroke *loop, int *idx, int max) {   /* ink strokes mostly inside the loop */
    int k = 0;
    for (int i = 0; i < nstrokes && k < max; i++) {
        const struct stroke *s = &strokes[i];
        if (s == loop || !is_ink(s->tool)) continue;
        int in = 0; for (int q = 0; q < s->n; q++) in += inside(s->p[q].x, s->p[q].y, loop);
        if (in >= 0.6 * s->n) idx[k++] = i;
    }
    return k;
}

/* ---------- strokes as a picture (gray, black lines) and PNG bytes for Gemini ---------- */
static void dot(unsigned char *img, int W, int H, float cx, float cy, float r) {
    for (int y = (int)floorf(cy - r); y <= (int)ceilf(cy + r); y++) for (int x = (int)floorf(cx - r); x <= (int)ceilf(cx + r); x++)
        if (x >= 0 && y >= 0 && x < W && y < H && (x - cx) * (x - cx) + (y - cy) * (y - cy) <= r * r) img[y * W + x] = 0;
}
static void draw_stroke(unsigned char *img, int W, int H, const struct stroke *s, float ox, float oy, float scale, float r) {
    for (int i = 0; i < s->n; i++) {
        float x = (s->p[i].x - ox) * scale, y = (s->p[i].y - oy) * scale;
        if (i == 0) { dot(img, W, H, x, y, r); continue; }
        float px = (s->p[i - 1].x - ox) * scale, py = (s->p[i - 1].y - oy) * scale, d = hypotf(x - px, y - py);
        int steps = (int)(d / (r * 0.6f)) + 1;
        for (int k = 1; k <= steps; k++) { float t = (float)k / steps; dot(img, W, H, px + (x - px) * t, py + (y - py) * t, r); }
    }
}
static size_t chunk_mem(unsigned char *o, const char *type, const unsigned char *data, size_t n) {
    be32(o, (uint32_t)n); memcpy(o + 4, type, 4); if (n) memcpy(o + 8, data, n);
    uint32_t c = crc32_(0, (const unsigned char *)type, 4); c = crc32_(c, data, n);
    be32(o + 8 + n, c);
    return 12 + n;
}
static size_t png_bytes(const unsigned char *img, int W, int H, unsigned char **out) {
    size_t raw_n = (size_t)H * (W + 1); unsigned char *raw = malloc(raw_n);
    for (int y = 0; y < H; y++) { raw[y * (W + 1)] = 0; memcpy(raw + y * (W + 1) + 1, img + (size_t)y * W, W); }
    size_t zcap = raw_n + raw_n / 4 + 64; unsigned char *z = malloc(zcap);
    size_t zn = zlib_stream(raw, raw_n, z, zcap);
    unsigned char *o = malloc(zn + 64); size_t p = 8;
    memcpy(o, "\x89PNG\r\n\x1a\n", 8);
    unsigned char ihdr[13] = {0}; be32(ihdr, W); be32(ihdr + 4, H); ihdr[8] = 8; ihdr[9] = 0;
    p += chunk_mem(o + p, "IHDR", ihdr, 13); p += chunk_mem(o + p, "IDAT", z, zn); p += chunk_mem(o + p, "IEND", NULL, 0);
    free(raw); free(z); *out = o;
    return p;
}
static size_t b64(const unsigned char *in, size_t n, char *out) {
    static const char T[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    size_t o = 0;
    for (size_t i = 0; i < n; i += 3) {
        uint32_t v = (uint32_t)in[i] << 16 | (i + 1 < n ? (uint32_t)in[i + 1] << 8 : 0) | (i + 2 < n ? in[i + 2] : 0);
        out[o++] = T[v >> 18 & 63]; out[o++] = T[v >> 12 & 63]; out[o++] = i + 1 < n ? T[v >> 6 & 63] : '='; out[o++] = i + 2 < n ? T[v & 63] : '=';
    }
    out[o] = 0;
    return o;
}

/* ---------- Gemini: one request, plain-text answer ---------- */
static size_t json_escape(const char *s, char *out, size_t cap) {
    size_t o = 0;
    for (const unsigned char *p = (const unsigned char *)s; *p && o + 8 < cap; p++) {
        if (*p == '"' || *p == '\\') { out[o++] = '\\'; out[o++] = *p; }
        else if (*p == '\n') { out[o++] = '\\'; out[o++] = 'n'; }
        else if (*p == '\r') continue;
        else if (*p == '\t') { out[o++] = '\\'; out[o++] = 't'; }
        else if (*p < 0x20) continue;
        else out[o++] = *p;
    }
    out[o] = 0;
    return o;
}
static void put_utf8(uint32_t c, char *out, size_t *i) {
    if (c < 0x80) out[(*i)++] = (char)c;
    else if (c < 0x800) { out[(*i)++] = (char)(0xC0 | c >> 6); out[(*i)++] = (char)(0x80 | (c & 0x3F)); }
    else if (c < 0x10000) { out[(*i)++] = (char)(0xE0 | c >> 12); out[(*i)++] = (char)(0x80 | (c >> 6 & 0x3F)); out[(*i)++] = (char)(0x80 | (c & 0x3F)); }
    else { out[(*i)++] = (char)(0xF0 | c >> 18); out[(*i)++] = (char)(0x80 | (c >> 12 & 0x3F)); out[(*i)++] = (char)(0x80 | (c >> 6 & 0x3F)); out[(*i)++] = (char)(0x80 | (c & 0x3F)); }
}
static int junquote(const char *p, char *out, size_t cap) {   /* a JSON string at p (opening quote) -> UTF-8 text */
    if (*p != '"') return 0;
    p++;
    size_t i = 0;
    while (*p && *p != '"' && i + 8 < cap) {
        if (*p != '\\') { out[i++] = *p++; continue; }
        p++;
        if (*p == 'n') { out[i++] = '\n'; p++; }
        else if (*p == 't') { out[i++] = '\t'; p++; }
        else if (*p == 'r' || *p == 'b' || *p == 'f') p++;
        else if (*p == 'u' && strlen(p) >= 5) {
            char h[5]; memcpy(h, p + 1, 4); h[4] = 0; uint32_t c = (uint32_t)strtoul(h, NULL, 16); p += 5;
            if (c >= 0xD800 && c < 0xDC00 && p[0] == '\\' && p[1] == 'u' && strlen(p) >= 6) { memcpy(h, p + 2, 4); uint32_t lo = (uint32_t)strtoul(h, NULL, 16); c = 0x10000 + ((c - 0xD800) << 10) + (lo - 0xDC00); p += 6; }
            put_utf8(c, out, &i);
        }
        else if (*p) out[i++] = *p++;   /* \" \\ \/ */
    }
    out[i] = 0;
    return 1;
}
static const char *GHOST = "generativelanguage.googleapis.com";
/* the answer text of Gemini for `prompt` and up to two PNGs, trying the models in turn; 1 on success */
static int ask_gemini(const char *prompt, const unsigned char *png1, size_t n1, const unsigned char *png2, size_t n2, char *text, size_t cap) {
    size_t bcap = strlen(prompt) * 2 + (n1 + n2) * 4 / 3 + 2048;
    char *body = malloc(bcap); size_t o = 0;
    o += snprintf(body + o, bcap - o, "{\"contents\":[{\"parts\":[{\"text\":\"");
    o += json_escape(prompt, body + o, bcap - o);
    o += snprintf(body + o, bcap - o, "\"}");
    const unsigned char *pngs[2] = {png1, png2}; size_t ns[2] = {n1, n2};
    for (int k = 0; k < 2; k++) if (pngs[k]) {
        o += snprintf(body + o, bcap - o, ",{\"inline_data\":{\"mime_type\":\"image/png\",\"data\":\"");
        o += b64(pngs[k], ns[k], body + o);
        o += snprintf(body + o, bcap - o, "\"}}");
    }
    o += snprintf(body + o, bcap - o, "]}]}");
    char *req = malloc(o + 1024), list[400]; strcpy(list, models);
    int ok = 0;
    for (char *model = strtok(list, ", "); model && !ok; model = strtok(NULL, ", ")) {
        int h = snprintf(req, o + 1024, "POST /v1beta/models/%s:generateContent?key=%s HTTP/1.0\r\nHost: %s\r\nContent-Type: application/json\r\n"
                         "User-Agent: remarkable-ai/0.1\r\nContent-Length: %zu\r\n\r\n", model, key, GHOST, o);
        memcpy(req + h, body, o + 1);
        long long t0 = now_us();
        int st = https(GHOST, req, resp, RESP_CAP);
        const char *c = st == 200 ? strstr(resp, "\"candidates\"") : NULL, *t = c ? jkey(c, "text") : NULL;
        if (t && junquote(t, text, cap) && text[0]) { fprintf(stderr, "%s answered in %.0f s\n", model, (now_us() - t0) / 1e6); ok = 1; break; }
        char msg[200] = ""; if (st > 0) jstr(resp, "message", msg, sizeof msg);
        fprintf(stderr, "%s: HTTP %d %s\n", model, st, msg);
        if (st == 400 || st == 401 || st == 403) break;   /* the key, not the load: no other model will help */
    }
    free(body); free(req);
    return ok;
}

/* ---------- printing the lesson: glyph atlases, Arabic joining, right-to-left lines, page images ---------- */
struct ug { uint32_t key; int16_t l, t, w, h, adv; unsigned char *px; };
struct uatlas { int size, bold, n; struct ug *g; };
static struct uatlas uat[6]; static int nuat = 0;

static struct uatlas *ufont(int size, int bold) {
    for (int i = 0; i < nuat; i++) if (uat[i].size == size && uat[i].bold == bold) return &uat[i];
    if (nuat == 6) return NULL;
    char path[700]; snprintf(path, sizeof path, "%s/f%d%c.atlas", dir, size, bold ? 'b' : 'r');
    size_t len; unsigned char *blob = (unsigned char *)slurp(path, &len);
    if (!blob || len < 8 || memcmp(blob, "ATLU", 4)) { fprintf(stderr, "no font atlas %s\n", path); free(blob); return NULL; }
    struct uatlas *a = &uat[nuat]; a->size = size; a->bold = bold; a->n = (int)u32(blob + 4); a->g = calloc(a->n > 0 ? a->n : 1, sizeof *a->g);
    size_t p = 8; int i;
    for (i = 0; i < a->n && p + 14 <= len; i++) {
        struct ug *g = &a->g[i]; g->key = u32(blob + p); p += 4;
        int16_t v[5]; for (int k = 0; k < 5; k++) { v[k] = (int16_t)(blob[p] | blob[p + 1] << 8); p += 2; }
        g->l = v[0]; g->t = v[1]; g->w = v[2]; g->h = v[3]; g->adv = v[4];
        size_t npx = (size_t)g->w * g->h;
        if (npx && p + npx <= len) { g->px = malloc(npx); memcpy(g->px, blob + p, npx); }
        p += npx;
    }
    a->n = i; free(blob); nuat++;
    return a;
}
static struct ug *uglyph(struct uatlas *a, uint32_t key) {   /* keys sorted by the baker */
    int lo = 0, hi = a->n - 1;
    while (lo <= hi) { int m = (lo + hi) / 2; if (a->g[m].key == key) return &a->g[m]; if (a->g[m].key < key) lo = m + 1; else hi = m - 1; }
    return NULL;
}

static unsigned char *page; static int cur_y, lesson_no, lesson_pages;
static void page_new(void) { if (!page) page = malloc((size_t)PW * PH); memset(page, 255, (size_t)PW * PH); cur_y = TOP; }
static int page_flush(void) {   /* the page as lessons/NNNN-<p>.z */
    lesson_pages++;
    char path[700]; snprintf(path, sizeof path, "%s/%04d-%d.z", lessons_dir, lesson_no, lesson_pages);
    size_t n = (size_t)PW * PH, zcap = n + n / 4 + 64; unsigned char *z = malloc(zcap);
    size_t zn = zlib_stream(page, n, z, zcap);
    FILE *f = fopen(path, "wb"); if (!f) { free(z); return 0; }
    fwrite(z, 1, zn, f); fclose(f); free(z);
    return 1;
}
static void stamp(const struct ug *g, int x, int y, int gray) {
    for (int j = 0; j < g->h; j++) for (int i = 0; i < g->w; i++) {
        int px = x + g->l + i, py = y + g->t + j; if (px < 0 || py < 0 || px >= PW || py >= PH) continue;
        int cov = g->px[j * g->w + i]; unsigned char *d = &page[py * PW + px];
        *d = (unsigned char)((*d * (255 - cov) + gray * cov) / 255);
    }
}

static int utf8_next(const unsigned char **s, uint32_t *cp) {
    const unsigned char *p = *s; if (!*p) return 0;
    if (*p < 0x80) { *cp = *p; *s = p + 1; }
    else if ((*p & 0xE0) == 0xC0 && p[1]) { *cp = (*p & 0x1F) << 6 | (p[1] & 0x3F); *s = p + 2; }
    else if ((*p & 0xF0) == 0xE0 && p[1] && p[2]) { *cp = (*p & 0x0F) << 12 | (p[1] & 0x3F) << 6 | (p[2] & 0x3F); *s = p + 3; }
    else if ((*p & 0xF8) == 0xF0 && p[1] && p[2] && p[3]) { *cp = (uint32_t)(*p & 0x07) << 18 | (p[1] & 0x3F) << 12 | (p[2] & 0x3F) << 6 | (p[3] & 0x3F); *s = p + 4; }
    else { *cp = '?'; *s = p + 1; }
    return 1;
}
static int decode(const char *s, uint32_t *out, int max) { int n = 0; const unsigned char *p = (const unsigned char *)s; uint32_t c; while (n < max && utf8_next(&p, &c)) out[n++] = c; return n; }

/* Arabic joining: 0 none, 1 joins to the right only (alef, dal, re, vav ...), 2 both sides, 3 a mark (transparent), 4 ZWNJ, 5 ZWJ */
static int jclass(uint32_t c) {
    if (c == 0x200C) return 4;
    if (c == 0x200D) return 5;
    if ((c >= 0x064B && c <= 0x0652) || c == 0x0670 || c == 0x0654 || c == 0x0655) return 3;
    switch (c) { case 0x0622: case 0x0623: case 0x0624: case 0x0625: case 0x0627: case 0x0629: case 0x062F: case 0x0630: case 0x0631: case 0x0632: case 0x0648: case 0x0649: case 0x0698: return 1; }
    if ((c >= 0x0626 && c <= 0x064A) || c == 0x0640 || c == 0x067E || c == 0x0686 || c == 0x06A9 || c == 0x06AF || c == 0x06CC || c == 0x06C0 || c == 0x06BE) return 2;
    return 0;
}
static char dclass(uint32_t c) {   /* R: Arabic script, L: letters and digits, N: neutral */
    if ((c >= 0x0600 && c <= 0x06FF && !(c >= 0x0660 && c <= 0x0669) && !(c >= 0x06F0 && c <= 0x06F9)) || (c >= 0xFB50 && c <= 0xFEFF)) return 'R';
    if ((c >= '0' && c <= '9') || (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= 0xC0 && c <= 0x24F) || (c >= 0x0660 && c <= 0x0669) || (c >= 0x06F0 && c <= 0x06F9)) return 'L';
    return 'N';
}
static uint32_t mirror(uint32_t c) {
    switch (c) { case '(': return ')'; case ')': return '('; case '[': return ']'; case ']': return '['; case '{': return '}'; case '}': return '{'; case '<': return '>'; case '>': return '<'; case 0xAB: return 0xBB; case 0xBB: return 0xAB; }
    return c;
}
/* glyphs of cp[s..e) in logical order, Arabic forms (from the neighbours in cp[0..n)) and the lam-alef
 * ligature resolved, brackets mirrored in a right-to-left run; glyphs the atlas lacks (emoji) are skipped */
static int run_glyphs(struct uatlas *a, const uint32_t *cp, int n, int s, int e, int rtl, struct ug **out, int max) {
    int k = 0;
    for (int i = s; i < e && k < max; i++) {
        uint32_t c = cp[i]; int jc = jclass(c);
        if (jc >= 3 || c == 0xFE0E || c == 0xFE0F) continue;
        int prev = 0, next = 0;
        if (jc == 1 || jc == 2) {
            for (int q = i - 1; q >= 0; q--) { int t = jclass(cp[q]); if (t == 3) continue; prev = (t == 2 || t == 5); break; }
            if (jc == 2) for (int q = i + 1; q < n; q++) { int t = jclass(cp[q]); if (t == 3) continue; next = (t == 1 || t == 2 || t == 5); break; }
        }
        int alef = -1;
        if (c == 0x0644 && i + 1 < e) switch (cp[i + 1]) { case 0x0622: alef = 0; break; case 0x0623: alef = 1; break; case 0x0625: alef = 2; break; case 0x0627: alef = 3; break; }
        uint32_t key;
        if (alef >= 0) { key = 0xFEF5 + 2 * alef + (prev ? 1 : 0); i++; }
        else { int form = prev && next ? 2 : prev ? 3 : next ? 1 : 0; key = (uint32_t)form << 21 | (rtl ? mirror(c) : c); }
        struct ug *g = uglyph(a, key);
        if (!g && (key >> 21)) g = uglyph(a, key & 0x1FFFFF);
        if (g) out[k++] = g;
    }
    return k;
}
/* one visual line of cp[0..n): neutrals take the direction of equal neighbours, else the paragraph's;
 * runs are placed right-to-left (right-aligned) in a Farsi paragraph, left-to-right otherwise, and a
 * right-to-left run is drawn from its right end. Returns the width; draws unless measure_only. */
static int draw_visual(struct uatlas *a, const uint32_t *cp, int n, int rtl, int y, int gray, int measure_only) {
    static char d[MAXCP]; static struct ug *gs[MAXCP];
    for (int i = 0; i < n; i++) d[i] = dclass(cp[i]);
    for (int i = 0; i < n; ) {
        if (d[i] != 'N') { i++; continue; }
        int j = i; while (j < n && d[j] == 'N') j++;
        char before = i > 0 ? d[i - 1] : 0, after = j < n ? d[j] : 0;
        char dir = (before && before == after) ? before : (rtl ? 'R' : 'L');
        for (int q = i; q < j; q++) d[q] = dir;
        i = j;
    }
    int x = rtl ? RIGHT : LEFT, total = 0;
    for (int i = 0; i < n; ) {
        int j = i; while (j < n && d[j] == d[i]) j++;
        int isr = d[i] == 'R';
        int k = run_glyphs(a, cp, n, i, j, isr, gs, MAXCP), w = 0;
        for (int q = 0; q < k; q++) w += gs[q]->adv;
        total += w;
        if (!measure_only) {
            int x0 = rtl ? x - w : x;
            if (isr) { int c = x0 + w; for (int q = 0; q < k; q++) { c -= gs[q]->adv; stamp(gs[q], c, y, gray); } }
            else { int c = x0; for (int q = 0; q < k; q++) { stamp(gs[q], c, y, gray); c += gs[q]->adv; } }
        }
        x = rtl ? x - w : x + w;
        i = j;
    }
    return total;
}
static int is_rtl(const uint32_t *cp, int n) {   /* a Farsi line: Persian letters at least half as many as Latin ones */
    int fa = 0, la = 0;
    for (int i = 0; i < n; i++) { if (cp[i] >= 0x0600 && cp[i] <= 0x06FF) fa++; else if ((cp[i] >= 'A' && cp[i] <= 'Z') || (cp[i] >= 'a' && cp[i] <= 'z')) la++; }
    return fa > 0 && fa * 2 >= la;
}
/* a paragraph wrapped on spaces to the column, one visual line per row, new pages as needed */
static void paragraph(struct uatlas *a, const uint32_t *cp, int n, int rtl, int gray, int lineh) {
    int start = 0;
    while (start < n) {
        while (start < n && cp[start] == ' ') start++;
        if (start >= n) break;
        int line_end = start, probe = start;
        while (probe < n) {
            int we = probe; while (we < n && cp[we] != ' ') we++;
            if (draw_visual(a, cp + start, we - start, rtl, 0, 0, 1) > RIGHT - LEFT && line_end > start) break;
            line_end = we;
            probe = we; while (probe < n && cp[probe] == ' ') probe++;
        }
        if (cur_y > BOTTOM - 40) { page_flush(); page_new(); }
        draw_visual(a, cp + start, line_end - start, rtl, cur_y, gray, 0);
        cur_y += lineh;
        start = line_end;
    }
}
static void strip_md(char *s) {   /* "**" out, ends trimmed */
    char *o = s;
    for (char *p = s; *p; p++) { if (p[0] == '*' && p[1] == '*') { p++; continue; } *o++ = *p; }
    *o = 0;
    while (o > s && (o[-1] == ' ' || o[-1] == '\t' || o[-1] == '\r')) *--o = 0;
    char *b = s; while (*b == ' ' || *b == '\t') b++;
    if (b != s) memmove(s, b, strlen(b) + 1);
}
static const char *HEADS[] = {"Definition & Meaning", "Structure & Grammar", "Examples & Translations", "Synonyms & Antonyms", "Connection to Previous", NULL};
/* the lesson printed as page image(s) of lesson `no`; returns the page count (0: no atlases) */
static int render_lesson(int no, const char *phrase, const char *context, const char *source, const char *lesson) {
    struct uatlas *title = ufont(60, 1), *head = ufont(34, 1), *body = ufont(30, 0), *meta = ufont(22, 0);
    if (!title || !head || !body || !meta) return 0;
    static uint32_t cp[MAXCP];
    lesson_no = no; lesson_pages = 0;
    page_new();
    int n = decode(phrase, cp, MAXCP);
    draw_visual(title, cp, n, 0, cur_y, 0, 0); cur_y += 80;
    char m[300], day[16]; time_t t = time(NULL); struct tm lt; localtime_r(&t, &lt); strftime(day, sizeof day, "%Y-%m-%d", &lt);
    snprintf(m, sizeof m, "%s  \xE2\x80\xA2  %s", source && *source ? source : "Words", day);
    n = decode(m, cp, MAXCP); draw_visual(meta, cp, n, 0, cur_y, 110, 0); cur_y += 44;
    if (context && *context && strcmp(context, "-")) { n = decode(context, cp, MAXCP); paragraph(meta, cp, n, is_rtl(cp, n), 80, 30); }
    cur_y += 20;
    memset(page + (size_t)cur_y * PW + LEFT, 0, RIGHT - LEFT); memset(page + (size_t)(cur_y + 1) * PW + LEFT, 0, RIGHT - LEFT);
    cur_y += 26;
    const char *p = lesson;
    while (*p) {
        const char *nl = strchr(p, '\n'); size_t len = nl ? (size_t)(nl - p) : strlen(p);
        char line[4096]; snprintf(line, sizeof line, "%.*s", (int)(len < sizeof line - 1 ? len : sizeof line - 1), p);
        p += len; if (*p == '\n') p++;
        strip_md(line);
        if (!line[0]) { cur_y += 16; continue; }
        int is_head = 0; for (int h = 0; HEADS[h]; h++) if (strstr(line, HEADS[h])) is_head = 1;
        n = decode(line, cp, MAXCP);
        if (is_head) cur_y += 12;
        paragraph(is_head ? head : body, cp, n, is_rtl(cp, n), 0, is_head ? 46 : 40);
        if (is_head) cur_y += 6;
    }
    page_flush();
    return lesson_pages;
}

/* ---------- lessons on disk, the Vocabulary document ---------- */
static int cmp_name(const void *a, const void *b) { return strcmp((const char *)a, (const char *)b); }
static int list_suffix(const char *d, const char *suffix, char (*names)[64], int max) {   /* sorted names ending in suffix */
    DIR *dp = opendir(d); if (!dp) return 0;
    struct dirent *e; int n = 0; size_t sl = strlen(suffix);
    while ((e = readdir(dp)) && n < max) { size_t l = strlen(e->d_name); if (l > sl && l < 64 && !strcmp(e->d_name + l - sl, suffix)) strcpy(names[n++], e->d_name); }
    closedir(dp);
    qsort(names, n, 64, cmp_name);
    return n;
}
static char names[4096][64];
static int lessons_count(void) { return list_suffix(lessons_dir, ".txt", names, 4096); }
static int next_no(void) { int n = lessons_count(), best = 0; for (int i = 0; i < n; i++) { int v = atoi(names[i]); if (v > best) best = v; } return best + 1; }
static void previous_words(char *out, size_t cap) {   /* the last 12 phrases, for the lesson's connection section */
    int n = lessons_count(); out[0] = 0;
    for (int i = n > 12 ? n - 12 : 0; i < n; i++) {
        char path[700]; snprintf(path, sizeof path, "%s/%s", lessons_dir, names[i]);
        FILE *f = fopen(path, "r"); if (!f) continue;
        char line[300]; if (fgets(line, sizeof line, f)) { line[strcspn(line, "\r\n")] = 0; if (line[0]) snprintf(out + strlen(out), cap - strlen(out), "%s%s", out[0] ? ", " : "", line); }
        fclose(f);
    }
}
static int save_lesson(int no, const char *phrase, const char *context, const char *source, const char *lesson) {
    char path[700]; snprintf(path, sizeof path, "%s/%04d.txt", lessons_dir, no);
    FILE *f = fopen(path, "w"); if (!f) return 0;
    fprintf(f, "%s\n%s\n%s\n\n%s\n", phrase, context && *context ? context : "-", source, lesson); fclose(f);
    return 1;
}
static int build_pdf(const char *out) {   /* every lesson page, in order, as one PDF; returns the page count */
    static char pages[4096][64];
    int n = list_suffix(lessons_dir, ".z", pages, 4096); if (!n) return 0;
    FILE *o = fopen(out, "wb"); if (!o) return 0;
    long *off = calloc(3 * n + 3, sizeof *off), pos = 0;
    pos += fprintf(o, "%%PDF-1.4\n");
    off[1] = pos; pos += fprintf(o, "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n");
    off[2] = pos; pos += fprintf(o, "2 0 obj << /Type /Pages /Count %d /Kids [", n);
    for (int i = 0; i < n; i++) pos += fprintf(o, "%d 0 R ", 3 + 3 * i);
    pos += fprintf(o, "] >> endobj\n");
    for (int i = 0; i < n; i++) {
        int b = 3 + 3 * i; char cs[100]; int cl = snprintf(cs, sizeof cs, "q %.2f 0 0 %.2f 0 0 cm /Im Do Q", PW * PTS, PH * PTS);
        off[b] = pos; pos += fprintf(o, "%d 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] /Contents %d 0 R /Resources << /XObject << /Im %d 0 R >> >> >> endobj\n", b, PW * PTS, PH * PTS, b + 1, b + 2);
        off[b + 1] = pos; pos += fprintf(o, "%d 0 obj << /Length %d >> stream\n%s\nendstream endobj\n", b + 1, cl, cs);
        char path[700]; snprintf(path, sizeof path, "%s/%s", lessons_dir, pages[i]);
        size_t zl; char *z = slurp(path, &zl);
        off[b + 2] = pos; pos += fprintf(o, "%d 0 obj << /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode /Length %zu >> stream\n", b + 2, PW, PH, zl);
        if (z) { pos += fwrite(z, 1, zl, o); free(z); }
        pos += fprintf(o, "\nendstream endobj\n");
    }
    long xref = pos; int total = 3 + 3 * n;
    fprintf(o, "xref\n0 %d\n0000000000 65535 f \n", total);
    for (int i = 1; i < total; i++) fprintf(o, "%010ld 00000 n \n", off[i]);
    fprintf(o, "trailer << /Size %d /Root 1 0 R >>\nstartxref\n%ld\n%%%%EOF\n", total, xref);
    fclose(o); free(off);
    return n;
}
static void clear_rm(const char *d) {   /* the pen layer of a closed document: its page files */
    DIR *dp = opendir(d); if (!dp) return;
    struct dirent *e; char path[1300];
    while ((e = readdir(dp))) { size_t l = strlen(e->d_name); if (l > 3 && !strcmp(e->d_name + l - 3, ".rm")) { snprintf(path, sizeof path, "%s/%s", d, e->d_name); unlink(path); } }
    closedir(dp);
}
static void touch_metadata(const char *base) {   /* lastModified = now, so the library shows the document as fresh */
    char path[700]; snprintf(path, sizeof path, "%s.metadata", base);
    size_t n; char *m = slurp(path, &n); if (!m) return;
    char *p = strstr(m, "\"lastModified\": \""); if (!p) { free(m); return; }
    p += 17; char *q = p; while (*q >= '0' && *q <= '9') q++;
    FILE *f = fopen(path, "w"); if (f) { fwrite(m, 1, p - m, f); fprintf(f, "%lld", (long long)time(NULL) * 1000); fputs(q, f); fclose(f); }
    free(m);
}
static int page_loops = 0, ndone = 0;
static void rebuild_if_due(void) {   /* the document gets every lesson so far, while nothing is open on the tablet */
    int have = lessons_count(); char v[32]; int pushed = read_kv("state", "pushed", v, sizeof v) ? atoi(v) : -1;
    if (!have || have == pushed || !home_screen()) return;
    char base[600], tmp[700]; snprintf(base, sizeof base, "%.*s", (int)(strlen(pdfpath) - 4), pdfpath); snprintf(tmp, sizeof tmp, "%s.new", pdfpath);
    int pages = build_pdf(tmp);
    if (!pages || rename(tmp, pdfpath)) { fprintf(stderr, "could not build the Vocabulary document\n"); unlink(tmp); return; }
    char path[700]; snprintf(path, sizeof path, "%s.content", base);
    FILE *f = fopen(path, "w");
    if (f) { fprintf(f, "{\"extraMetadata\": {}, \"fileType\": \"pdf\", \"formatVersion\": 2, \"lineHeight\": -1, \"margins\": 0, \"orientation\": \"portrait\", \"pageCount\": %d, \"textScale\": 1, \"zoomMode\": \"bestFit\"}\n", pages); fclose(f); }
    clear_rm(base);
    touch_metadata(base);
    int cleared = 0;
    if (ndone > 0 && page_loops == ndone) { clear_rm(wdir); unlink(done_path); ndone = 0; page_loops = 0; cleared = 1; }   /* every loop on the Words page became a lesson */
    snprintf(path, sizeof path, "%s/state", dir);
    f = fopen(path, "w"); if (f) { fprintf(f, "pushed=%d\n", have); fclose(f); }
    fprintf(stderr, "Vocabulary document rebuilt: %d lessons, %d pages%s; restarting xochitl\n", have, pages, cleared ? ", Words page cleared" : "");
    if (!getenv("RM_FIXTURES")) { if (system("systemctl restart xochitl")) fprintf(stderr, "restart failed\n"); }
}

/* ---------- the Words page: loops done, lessons made ---------- */
struct sig { int x0, y0, x1, y1, n; };
static struct sig done[512];
static struct sig sig_of(const struct stroke *s) { struct sig g = {(int)s->x0, (int)s->y0, (int)s->x1, (int)s->y1, s->n}; return g; }
static int same_sig(struct sig a, struct sig b) { return a.x0 == b.x0 && a.y0 == b.y0 && a.x1 == b.x1 && a.y1 == b.y1 && a.n == b.n; }
static void save_done(void) { FILE *f = fopen(done_path, "w"); if (!f) return; for (int i = 0; i < ndone; i++) fprintf(f, "%d %d %d %d %d\n", done[i].x0, done[i].y0, done[i].x1, done[i].y1, done[i].n); fclose(f); }
static void load_done(void) { FILE *f = fopen(done_path, "r"); if (!f) return; struct sig s; while (ndone < 512 && fscanf(f, "%d %d %d %d %d", &s.x0, &s.y0, &s.x1, &s.y1, &s.n) == 5) done[ndone++] = s; fclose(f); }
static int is_done(struct sig s) { for (int i = 0; i < ndone; i++) if (same_sig(done[i], s)) return 1; return 0; }
static struct { struct sig s; time_t after; } failed[64]; static int nfailed;
static time_t retry_at(struct sig s) { for (int i = 0; i < nfailed; i++) if (same_sig(failed[i].s, s)) return failed[i].after; return 0; }
static void set_failed(struct sig s, time_t after) { for (int i = 0; i < nfailed; i++) if (same_sig(failed[i].s, s)) { failed[i].after = after; return; } if (nfailed < 64) { failed[nfailed].s = s; failed[nfailed++].after = after; } }

static char *teacher; static char prompt[1 << 16], answer[1 << 16];
static void prompt_head(void) {
    char prev[2000]; previous_words(prev, sizeof prev);
    snprintf(prompt, sizeof prompt, "%s\n\n", teacher ? teacher : "You are an experienced English teacher for adult Farsi speakers at an intermediate level. Explain the word or phrase as a patient tutor would, in simple English, with usage and examples, and give the Farsi.");
    if (prev[0]) snprintf(prompt + strlen(prompt), sizeof prompt - strlen(prompt), "Words learned earlier, for the connection section: %s.\n", prev);
}
static const char *REPLY = "Reply as plain text without markdown, exactly in this form:\nPHRASE: <the word or phrase, spelling corrected>\nCONTEXT: <the sentence it was used in, or - if there is none>\n\n<the complete lesson in the shape the instructions above describe; write each section heading in English followed by the Farsi in parentheses, no emoji>";
/* the answer split into phrase, context and lesson; a lesson is made of it. Returns 1 when saved. */
static int lesson_from_answer(const char *source) {
    char phrase[300] = "", context[2000] = "";
    const char *p = answer;
    if (!strncmp(p, "PHRASE:", 7)) { p += 7; while (*p == ' ') p++; snprintf(phrase, sizeof phrase, "%.*s", (int)strcspn(p, "\n"), p); p += strcspn(p, "\n"); if (*p) p++; }
    if (!strncmp(p, "CONTEXT:", 8)) { p += 8; while (*p == ' ') p++; snprintf(context, sizeof context, "%.*s", (int)strcspn(p, "\n"), p); p += strcspn(p, "\n"); if (*p) p++; }
    while (*p == '\n' || *p == '\r') p++;
    if (!phrase[0]) { snprintf(phrase, sizeof phrase, "%.*s", (int)strcspn(p, "\n"), p); p += strcspn(p, "\n"); while (*p == '\n') p++; }
    strip_md(phrase);
    int no = next_no();
    if (!save_lesson(no, phrase, context, source, p)) return 0;
    int pages = render_lesson(no, phrase, context, source, p);
    fprintf(stderr, "lesson %04d '%s': %d page(s)\n", no, phrase, pages);
    return pages > 0;
}
static int make_lesson_from_loop(const struct stroke *loop, int *idx, int nc) {
    float x0 = loop->x0 - 20, y0 = loop->y0 - 20; int W1 = (int)((loop->x1 - loop->x0 + 40) * 2), H1 = (int)((loop->y1 - loop->y0 + 40) * 2);
    unsigned char *im1 = malloc((size_t)W1 * H1); memset(im1, 255, (size_t)W1 * H1);
    for (int i = 0; i < nc; i++) draw_stroke(im1, W1, H1, &strokes[idx[i]], x0, y0, 2, 3);
    int W2 = PW / 2, H2 = PH / 2; unsigned char *im2 = malloc((size_t)W2 * H2); memset(im2, 255, (size_t)W2 * H2);
    for (int i = 0; i < nstrokes; i++) if (is_ink(strokes[i].tool)) draw_stroke(im2, W2, H2, &strokes[i], 0, 0, 0.5f, 1.5f);
    unsigned char *png1, *png2; size_t n1 = png_bytes(im1, W1, H1, &png1), n2 = png_bytes(im2, W2, H2, &png2);
    free(im1); free(im2);
    prompt_head();
    snprintf(prompt + strlen(prompt), sizeof prompt - strlen(prompt), "The learner wrote by hand on a page and drew a loop around a word or phrase. The first image is the circled handwriting: "
             "the word or phrase to teach (read it carefully; correct an obvious misspelling). The second image is the whole page: if a sentence using that word is written "
             "on it, use it as the context.\n%s", REPLY);
    int ok = ask_gemini(prompt, png1, n1, png2, n2, answer, sizeof answer);
    free(png1); free(png2);
    return ok ? lesson_from_answer("Words") : 0;
}
static void check_mark(const struct stroke *loop) {   /* a tick beside the loop: the lesson is made */
    if (!page_on_screen() || !open_device()) return;
    stroke_file("check.bin", 0, (int)loop->x1 + 12, (int)loop->y0 - 6, -1);
    close_device();
}
static time_t inbox_retry = 0;
static void inbox_poll(void) {   /* lookups from the PC: phrase, context, source, one file each */
    static char files[64][64];
    if (time(NULL) < inbox_retry) return;
    int n = list_suffix(inbox_dir, ".txt", files, 64);
    for (int i = 0; i < n; i++) {
        char path[700]; snprintf(path, sizeof path, "%s/%s", inbox_dir, files[i]);
        char *t = slurp(path, NULL); if (!t) continue;
        char *l1 = t, *l2 = strchr(t, '\n'), *l3 = l2 ? strchr(l2 + 1, '\n') : NULL;
        if (l2) *l2++ = 0;
        if (l3) { *l3++ = 0; l3[strcspn(l3, "\n")] = 0; }
        prompt_head();
        snprintf(prompt + strlen(prompt), sizeof prompt - strlen(prompt), "The learner marked this word or phrase while reading: \"%s\".%s%s%s\n%s", l1,
                 l2 && *l2 && strcmp(l2, "-") ? " It was read in this sentence: \"" : "", l2 && *l2 && strcmp(l2, "-") ? l2 : "", l2 && *l2 && strcmp(l2, "-") ? "\"." : "", REPLY);
        fprintf(stderr, "lookup from the PC: '%s'\n", l1);
        int ok = ask_gemini(prompt, NULL, 0, NULL, 0, answer, sizeof answer) && lesson_from_answer(l3 && *l3 ? l3 : "PC");
        free(t);
        if (ok) unlink(path); else { inbox_retry = time(NULL) + RETRY_S; break; }
    }
}

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: rmvocab <dir> [xochitl.conf] [event device]\n"); return 1; }
    strncpy(dir, argv[1], sizeof dir - 1);
    parse_dev_args(argc, argv);
    if (!read_kv("config", "doc", doc, sizeof doc) || !read_kv("config", "pdf", pdfpath, sizeof pdfpath) || !read_kv("config", "wdir", wdir, sizeof wdir) || !read_kv("config", "words", words_doc, sizeof words_doc)) {
        fprintf(stderr, "config: doc=, pdf=, wdir= and words= are needed\n"); return 1;
    }
    read_kv("config", "models", models, sizeof models);
    char path[700]; snprintf(path, sizeof path, "%s/key", dir);
    char *k = slurp(path, NULL); if (!k || !k[0]) { fprintf(stderr, "no Gemini key in %s\n", path); return 1; }
    k[strcspn(k, "\r\n")] = 0; snprintf(key, sizeof key, "%s", k); free(k);
    snprintf(path, sizeof path, "%s/teacher.md", dir); teacher = slurp(path, NULL);
    snprintf(lessons_dir, sizeof lessons_dir, "%s/lessons", dir); snprintf(inbox_dir, sizeof inbox_dir, "%s/inbox", dir); snprintf(done_path, sizeof done_path, "%s/done", dir);
    mkdir(lessons_dir, 0755); mkdir(inbox_dir, 0755);
    https_timeout = 90;
    use_pc_timezone();
    setvbuf(stderr, NULL, _IOLBF, 0);
    journal_start(words_doc);
    load_done();
    fprintf(stderr, "watching the Words page in %s; %d lessons so far\n", wdir, lessons_count());
    char rmfile[700]; long seen = 0; int idx[4096];
    while (1) {
        /* the newest page file of the Words document is the page on screen */
        DIR *dp = opendir(wdir); long newest = 0; struct dirent *e;
        if (dp) { while ((e = readdir(dp))) { size_t l = strlen(e->d_name); if (l > 3 && !strcmp(e->d_name + l - 3, ".rm")) { char p[1300]; snprintf(p, sizeof p, "%s/%s", wdir, e->d_name); long m = mtime(p); if (m > newest) { newest = m; snprintf(rmfile, sizeof rmfile, "%s", p); } } } closedir(dp); }
        time_t now = time(NULL);
        for (int i = 0; i < nfailed; i++) if (failed[i].after && failed[i].after <= now) { failed[i].after = 0; seen = 0; }   /* look again */
        if (newest && newest != seen && now - newest >= 2) {
            seen = newest;
            int ns = read_page(rmfile);
            if (ns >= 0) fprintf(stderr, "Words page saved: %d strokes\n", ns);
            if (ns >= 0) {
                int loops = 0, kept = 0; struct sig present[512]; int npresent = 0;
                for (int i = 0; i < nstrokes; i++) {
                    if (!is_ink(strokes[i].tool) || !is_loop(&strokes[i])) continue;
                    int nc = content_of(&strokes[i], idx, 4096);
                    if (!nc) continue;
                    loops++;
                    struct sig s = sig_of(&strokes[i]);
                    if (npresent < 512) present[npresent++] = s;
                    if (is_done(s) || retry_at(s) > now) continue;
                    fprintf(stderr, "loop at %d,%d-%d,%d with %d strokes inside: asking Gemini\n", s.x0, s.y0, s.x1, s.y1, nc);
                    if (make_lesson_from_loop(&strokes[i], idx, nc)) { if (ndone < 512) done[ndone++] = s; save_done(); check_mark(&strokes[i]); }
                    else { fprintf(stderr, "no lesson this time, trying again in %d s\n", RETRY_S); set_failed(s, now + RETRY_S); }
                    seen = 0;   /* more loops may wait: the page is read again next round */
                    break;
                }
                for (int i = 0; i < ndone; i++) { int still = 0; for (int q = 0; q < npresent; q++) if (same_sig(done[i], present[q])) still = 1; if (still) done[kept++] = done[i]; }
                if (kept != ndone) { ndone = kept; save_done(); }   /* a loop erased by hand is forgotten */
                page_loops = loops;
            }
        }
        inbox_poll();
        rebuild_if_due();
        sleep(2);
    }
}
