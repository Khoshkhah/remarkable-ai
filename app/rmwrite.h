/* Writing reMarkable .rm v6 scene files, enough to add a layer of pen strokes to a page.
 *
 * The tablet needs this to draw a flashcard onto the page a word was circled on: a layer holds strokes
 * and never images (the scene items are Line, Text, GlyphRange, Rectangle), so the card is drawn.
 *
 * The format, as rmscene writes it (the reader in stylus.h walks the same framing):
 *   header   43 bytes "reMarkable .lines file, version=6          "
 *   block    uint32 payload length, uint8 0, uint8 min_version, uint8 current_version, uint8 type
 *   tag      varuint (index << 4 | type), type: ID 0xF, Length4 0xC, Byte8 0x8, Byte4 0x4, Byte1 0x1
 *   CrdtId   uint8 part1, varuint part2
 *   subblock tag(index, Length4), uint32 length, payload
 * A reader takes the blocks in order, so a layer is appended to a page by appending its blocks: the
 * file already on the tablet is never rewritten, only grown.
 */
#ifndef RMWRITE_H
#define RMWRITE_H

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#define RM_HEADER "reMarkable .lines file, version=6          "
#define RM_PAGE_W 1404
#define RM_PAGE_H 1872

#define RM_PEN_FINELINER 17
#define RM_PEN_MARKER    16
#define RM_COLOR_BLACK    0
#define RM_COLOR_GRAY     1
#define RM_COLOR_WHITE    2

/* ---- a growable buffer ------------------------------------------------------------------------- */
struct rmbuf { unsigned char *p; size_t n, cap; };

static void rb_need(struct rmbuf *b, size_t extra) {
    if (b->n + extra <= b->cap) return;
    size_t cap = b->cap ? b->cap : 256;
    while (cap < b->n + extra) cap *= 2;
    b->p = realloc(b->p, cap);
    b->cap = cap;
}
static void rb_bytes(struct rmbuf *b, const void *d, size_t n) { rb_need(b, n); memcpy(b->p + b->n, d, n); b->n += n; }
static void rb_u8(struct rmbuf *b, unsigned v) { unsigned char c = (unsigned char)v; rb_bytes(b, &c, 1); }
static void rb_u16(struct rmbuf *b, unsigned v) { unsigned char c[2] = {v & 0xff, (v >> 8) & 0xff}; rb_bytes(b, c, 2); }
static void rb_u32(struct rmbuf *b, uint32_t v) { unsigned char c[4] = {v & 0xff, v >> 8 & 0xff, v >> 16 & 0xff, v >> 24 & 0xff}; rb_bytes(b, c, 4); }
static void rb_varuint(struct rmbuf *b, uint64_t v) {
    do { unsigned char c = v & 0x7f; v >>= 7; if (v) c |= 0x80; rb_bytes(b, &c, 1); } while (v);
}
static void rb_f32(struct rmbuf *b, float v) { rb_bytes(b, &v, 4); }          /* little-endian ARM/x86 */
static void rb_f64(struct rmbuf *b, double v) { rb_bytes(b, &v, 8); }
static void rb_free(struct rmbuf *b) { free(b->p); b->p = NULL; b->n = b->cap = 0; }

/* ---- tagged values ----------------------------------------------------------------------------- */
#define RM_T_ID 0xF
#define RM_T_LEN4 0xC
#define RM_T_B8 0x8
#define RM_T_B4 0x4
#define RM_T_B1 0x1

static void rb_tag(struct rmbuf *b, int index, int type) { rb_varuint(b, (uint64_t)index << 4 | type); }
static void rb_id(struct rmbuf *b, int index, unsigned part1, uint64_t part2) {
    rb_tag(b, index, RM_T_ID); rb_u8(b, part1); rb_varuint(b, part2);
}
static void rb_bool(struct rmbuf *b, int index, int v) { rb_tag(b, index, RM_T_B1); rb_u8(b, v ? 1 : 0); }
static void rb_int(struct rmbuf *b, int index, uint32_t v) { rb_tag(b, index, RM_T_B4); rb_u32(b, v); }
static void rb_float(struct rmbuf *b, int index, float v) { rb_tag(b, index, RM_T_B4); rb_f32(b, v); }
static void rb_double(struct rmbuf *b, int index, double v) { rb_tag(b, index, RM_T_B8); rb_f64(b, v); }

static void rb_sub(struct rmbuf *b, int index, struct rmbuf *inner) {   /* consumes `inner` */
    rb_tag(b, index, RM_T_LEN4); rb_u32(b, (uint32_t)inner->n); rb_bytes(b, inner->p, inner->n);
    rb_free(inner);
}
static void rb_lww_string(struct rmbuf *b, int index, unsigned t1, uint64_t t2, const char *s) {
    struct rmbuf v = {0}, str = {0};
    rb_id(&v, 1, t1, t2);
    rb_varuint(&str, strlen(s)); rb_u8(&str, 1); rb_bytes(&str, s, strlen(s));
    rb_sub(&v, 2, &str);
    rb_sub(b, index, &v);
}
static void rb_lww_bool(struct rmbuf *b, int index, unsigned t1, uint64_t t2, int val) {
    struct rmbuf v = {0};
    rb_id(&v, 1, t1, t2); rb_bool(&v, 2, val);
    rb_sub(b, index, &v);
}

/* ---- blocks ------------------------------------------------------------------------------------ */
static void rb_block(struct rmbuf *out, int type, int min_version, int current_version, struct rmbuf *payload) {
    rb_u32(out, (uint32_t)payload->n);
    rb_u8(out, 0); rb_u8(out, min_version); rb_u8(out, current_version); rb_u8(out, type);
    rb_bytes(out, payload->p, payload->n);
    rb_free(payload);
}

/* the item header shared by every scene item block: where it hangs and where it sits among its siblings */
static void rb_item_head(struct rmbuf *p, uint64_t parent, uint64_t id, uint64_t left, uint64_t right) {
    rb_id(p, 1, 0, parent); rb_id(p, 2, 0, id); rb_id(p, 3, 0, left); rb_id(p, 4, 0, right);
    rb_int(p, 5, 0);
}

/* A layer: the node exists (SceneTreeBlock), it has a name (TreeNodeBlock), and it is one of the root's
 * children (SceneGroupItemBlock). `left` is the item id of the layer it follows, 0 for the first. */
static void rm_layer(struct rmbuf *out, uint64_t node, uint64_t label_ts, const char *name,
                     uint64_t root, uint64_t item, uint64_t left) {
    struct rmbuf p = {0}, sub = {0};
    rb_id(&p, 1, 0, node); rb_id(&p, 2, 0, 0); rb_bool(&p, 3, 1);
    rb_id(&sub, 1, 0, root); rb_sub(&p, 4, &sub);
    rb_block(out, 0x01, 1, 1, &p);

    rb_id(&p, 1, 0, node);
    rb_lww_string(&p, 2, 0, label_ts, name);
    rb_lww_bool(&p, 3, 0, 0, 1);
    rb_block(out, 0x02, 1, 1, &p);

    rb_item_head(&p, root, item, left, 0);
    rb_u8(&sub, 0x02); rb_id(&sub, 2, 0, node);     /* ITEM_TYPE 2: the value is the node id */
    rb_sub(&p, 6, &sub);
    rb_block(out, 0x04, 1, 1, &p);
}

struct rmpt { float x, y; };

/* One pen stroke. Display coordinates: v6 pages are centred on the origin, x runs -702..702. */
static void rm_line_c(struct rmbuf *out, uint64_t layer, uint64_t id, uint64_t left,
                      const struct rmpt *pts, int n, int tool, float width, int color) {
    struct rmbuf p = {0}, sub = {0}, points = {0};
    rb_item_head(&p, layer, id, left, 0);
    rb_u8(&sub, 0x03);                               /* ITEM_TYPE 3: a Line */
    rb_int(&sub, 1, (uint32_t)tool);
    rb_int(&sub, 2, (uint32_t)color);
    rb_double(&sub, 3, width);
    rb_float(&sub, 4, 0.0f);
    for (int i = 0; i < n; i++) {
        rb_f32(&points, pts[i].x - RM_PAGE_W / 2.0f);
        rb_f32(&points, pts[i].y);
        rb_u16(&points, 0);                          /* speed */
        rb_u16(&points, (unsigned)(width * 10));     /* width */
        rb_u8(&points, 0);                           /* direction */
        rb_u8(&points, 100);                         /* pressure */
    }
    rb_sub(&sub, 5, &points);
    rb_id(&sub, 6, 0, 1);                            /* the timestamp rmscene always writes after the points */
    rb_sub(&p, 6, &sub);
    rb_block(out, 0x05, 2, 2, &p);
}

static void rm_line(struct rmbuf *out, uint64_t layer, uint64_t id, uint64_t left,
                    const struct rmpt *pts, int n, int tool, float width) {
    rm_line_c(out, layer, id, left, pts, n, tool, width, RM_COLOR_BLACK);
}

/* The three blocks every page starts with. `author` is any 16 bytes. */
static void rm_page_head(struct rmbuf *out, const unsigned char author[16], uint64_t root) {
    struct rmbuf p = {0}, sub = {0};
    rb_varuint(&p, 1);
    rb_varuint(&sub, 16); rb_bytes(&sub, author, 16); rb_u16(&sub, 1);
    rb_sub(&p, 0, &sub);
    rb_block(out, 0x09, 1, 1, &p);

    rb_id(&p, 1, 1, 1); rb_bool(&p, 2, 1); rb_bool(&p, 3, 0);
    rb_block(out, 0x00, 1, 1, &p);

    rb_int(&p, 1, 1); rb_int(&p, 2, 0); rb_int(&p, 3, 0); rb_int(&p, 4, 0); rb_int(&p, 5, 0);
    rb_block(out, 0x0A, 1, 1, &p);

    rb_id(&p, 1, 0, root);                           /* the root node, unnamed */
    rb_lww_string(&p, 2, 0, 0, "");
    rb_lww_bool(&p, 3, 0, 0, 1);
    rb_block(out, 0x02, 1, 1, &p);
}

/* ---- reading back what is already on a page ----------------------------------------------------- */
/* Enough of the file to append to it safely: the largest CrdtId in use (ours start above it) and the
 * item id of the root's last child (the new layer goes after it). Returns 0 if the file is not v6. */
struct rmscan { uint64_t max_id, last_child, root; int layers; };

static int rm_scan(const char *path, struct rmscan *s) {
    memset(s, 0, sizeof *s);
    s->root = 1;
    FILE *f = fopen(path, "rb"); if (!f) return 0;
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    if (n < 43) { fclose(f); return 0; }
    unsigned char *buf = malloc((size_t)n);
    if (fread(buf, 1, (size_t)n, f) != (size_t)n) { fclose(f); free(buf); return 0; }
    fclose(f);
    if (memcmp(buf, RM_HEADER, 43)) { free(buf); return 0; }
    long p = 43;
    while (p + 8 <= n) {
        uint32_t len = buf[p] | buf[p+1] << 8 | buf[p+2] << 16 | (uint32_t)buf[p+3] << 24;
        int type = buf[p + 7];
        const unsigned char *b = buf + p + 8, *e = b + len;
        if (e > buf + n) break;
        /* every id we can reach cheaply: tag, part1, varuint part2 */
        int index = 0;
        while (b + 2 < e && index < 4) {
            if ((*b & 0xf) != RM_T_ID) break;
            int idx = *b >> 4; b++;
            unsigned part1 = *b++;
            uint64_t part2 = 0; int shift = 0;
            while (b < e) { part2 |= (uint64_t)(*b & 0x7f) << shift; shift += 7; if (!(*b++ & 0x80)) break; }
            if (part1 == 0 && part2 > s->max_id) s->max_id = part2;
            if (type == 0x02 && idx == 1) s->layers++;
            if (type == 0x04 && idx == 2) s->last_child = part2;   /* a child of the root: item id */
            index++;
        }
        p += 8 + len;
    }
    free(buf);
    return 1;
}

#endif
