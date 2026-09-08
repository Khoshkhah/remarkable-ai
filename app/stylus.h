/* stylus.h: the virtual pen on the reMarkable itself, shared by rmclock.c and rmdash.c.
 *
 * Strokes are pre-baked on the PC by rm_ai.py (StrokeRecorder) as raw 16-byte Linux input events,
 * byte for byte what the Python VirtualStylus writes, and replayed here with the pacing measured on
 * the device: one frame per 3 ms, a pause after every eraser stroke and on pen<->eraser switches.
 * A blob may hold several strokes; each stroke is retried until the real pen stays away, because
 * any digitizer event that is not an echo of our own means the user is writing.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <unistd.h>
#include <fcntl.h>
#include <time.h>
#include <math.h>
#include <sys/time.h>
#include <sys/stat.h>

#define EV_SYN 0
#define EV_KEY 1
#define EV_ABS 3
#define ABS_X 0
#define ABS_Y 1
#define BTN_TOOL_PEN 0x140
#define BTN_TOOL_RUBBER 0x141
#define BTN_TOUCH 0x14a
#define ABS_PRESSURE 0x18
#define ABS_DISTANCE 0x19

#define FRAME_US        3000        /* VirtualStylus.FRAME_DT */
#define TOOL_SETTLE_US  30000       /* VirtualStylus.TOOL_SETTLE */
#define ERASE_SETTLE_US 150000      /* VirtualStylus.ERASE_SETTLE */
#define START_SETTLE_US 4000000     /* DigitalClock.START_SETTLE */
#define YIELD_US        2500000     /* VirtualStylus.YIELD_SECONDS */
#define ECHO_KEEP_US    1500000
#define INK_TIMEOUT_S   180         /* no autosave of our strokes for this long (awake) = the page is not on screen */
#define MAXBLOB         (1 << 20)
#define MAXFRAME        32

struct ev { uint32_t sec, usec; uint16_t type, code; int32_t value; };   /* 16 bytes, the device's ABI */

static const char *CONF = "/home/root/.config/remarkable/xochitl.conf", *DEV = "/dev/input/event1";
static char dir[512];
static int fd = -1;
static long long real_until = 0;
static int last_tool = 0;   /* 0 none, 1 pen, 2 eraser */

/* echo filter: (type, code, value) of events we injected recently come back through the reader */
#define RING 4096
static struct { uint16_t type, code; int32_t value; long long t; } ring[RING];
static int ring_i = 0;

static long long now_us(void) { struct timeval tv; gettimeofday(&tv, NULL); return tv.tv_sec * 1000000LL + tv.tv_usec; }

static int is_echo(const struct ev *e) {
    long long t = now_us();
    for (int i = 0; i < RING; i++)
        if (ring[i].type == e->type && ring[i].code == e->code && ring[i].value == e->value && t - ring[i].t < ECHO_KEEP_US)
            return 1;
    return 0;
}

/* drain the digitizer; anything that is not our echo is the real pen */
static void watch_pen(void) {
    struct ev e;
    while (fd >= 0 && read(fd, &e, sizeof e) == (ssize_t)sizeof e) {
        if (e.type == EV_SYN || is_echo(&e)) continue;
        if (now_us() >= real_until) fprintf(stderr, "real pen near the screen, pausing\n");
        real_until = now_us() + YIELD_US;
    }
}

static int pen_near(void) { watch_pen(); return now_us() < real_until; }

static void nap(long us) {   /* sleep in slices, keeping the pen watch alive */
    while (us > 0) { watch_pen(); long s = us < 100000 ? us : 100000; usleep((useconds_t)s); us -= s; }
}

static void write_frame(const struct ev *ev, int n) {
    for (int i = 0; i < n; i++) {
        ring[ring_i].type = ev[i].type; ring[ring_i].code = ev[i].code; ring[ring_i].value = ev[i].value; ring[ring_i].t = now_us();
        ring_i = (ring_i + 1) % RING;
    }
    if (write(fd, ev, n * sizeof *ev) < 0) perror("write");
    usleep(FRAME_US);
    watch_pen();   /* drain our echo every frame so the device buffer never overflows and drops the real pen */
}

#define E(t, c, v) {0, 0, (t), (c), (v)}

static void lift(int tool_code) {
    struct ev ev[5] = {E(EV_ABS, ABS_PRESSURE, 0), E(EV_KEY, BTN_TOUCH, 0), E(EV_ABS, ABS_DISTANCE, 60), E(EV_KEY, tool_code, 0), E(EV_SYN, 0, 0)};
    write_frame(ev, 5);
}

static void lift_everything(void) {   /* pen up, both tools out: a run killed mid-stroke leaves the eraser 'down' */
    struct ev ev[6] = {E(EV_ABS, ABS_PRESSURE, 0), E(EV_KEY, BTN_TOUCH, 0), E(EV_ABS, ABS_DISTANCE, 100),
                       E(EV_KEY, BTN_TOOL_RUBBER, 0), E(EV_KEY, BTN_TOOL_PEN, 0), E(EV_SYN, 0, 0)};
    write_frame(ev, 6);
    last_tool = 0;
}

/* display px offsets -> digitizer units (VirtualStylus.display_to_digitizer: ABS_Y runs along x, ABS_X along -y) */
static int32_t units_x(int dx) { return (int32_t)lround(dx * 15725.0 / 1404); }
static int32_t units_y(int dy) { return (int32_t)-lround(dy * 20966.0 / 1872); }

/* replay one baked stroke, shifted by (ox, oy) digitizer units, frame by frame; a frame that would go
 * right of xmax (digitizer units along the display's x, -1 = no limit) ends the stroke there.
 * Returns 0 if the real pen interrupted it. */
static int play_stroke(const struct ev *ev, size_t n, int eraser, int32_t ox, int32_t oy, int32_t xmax) {
    int tool = eraser ? 2 : 1;
    if (last_tool && tool != last_tool) usleep(TOOL_SETTLE_US);
    last_tool = tool;
    struct ev frame[MAXFRAME];
    int k = 0;
    for (size_t i = 0; i < n; i++) {
        if (k < MAXFRAME) frame[k] = ev[i];
        if (ev[i].type == EV_ABS && ev[i].code == ABS_X) { int32_t v = ev[i].value + oy; frame[k].value = v < 0 ? 0 : v > 20966 ? 20966 : v; }
        if (ev[i].type == EV_ABS && ev[i].code == ABS_Y) {
            int32_t v = ev[i].value + ox;
            if (xmax >= 0 && v > xmax) { lift(eraser ? BTN_TOOL_RUBBER : BTN_TOOL_PEN); return 1; }
            frame[k].value = v < 0 ? 0 : v > 15725 ? 15725 : v;
        }
        if (k < MAXFRAME) k++;
        if (ev[i].type != EV_SYN) continue;
        if (pen_near()) { lift(eraser ? BTN_TOOL_RUBBER : BTN_TOOL_PEN); return 0; }
        write_frame(frame, k);
        k = 0;
    }
    if (eraser) usleep(ERASE_SETTLE_US);
    return 1;
}

static unsigned char *load(const char *name, size_t *len) {
    char path[1100]; snprintf(path, sizeof path, "%s/%s", dir, name);
    FILE *f = fopen(path, "rb"); if (!f) { *len = 0; return NULL; }
    unsigned char *buf = malloc(MAXBLOB); *len = fread(buf, 1, MAXBLOB, f); fclose(f); return buf;
}

/* every stroke in the blob, in order; each retried until it completes with the real pen away */
static void play_blob(const unsigned char *blob, size_t len, int eraser, int32_t ox, int32_t oy, int32_t xmax) {
    const struct ev *ev = (const struct ev *)blob;
    size_t n = len / sizeof *ev, start = 0;
    int tool_code = eraser ? BTN_TOOL_RUBBER : BTN_TOOL_PEN;
    for (size_t i = 0; i < n; i++) {
        if (!(ev[i].type == EV_KEY && ev[i].code == tool_code && ev[i].value == 0)) continue;
        size_t end = i + 1;                                   /* the stroke ends with the tool leaving proximity ... */
        while (end < n && ev[end].type != EV_SYN) end++;      /* ... and that frame's SYN */
        if (end < n) end++;
        while (1) {
            while (pen_near()) nap(100000);
            if (play_stroke(ev + start, end - start, eraser, ox, oy, xmax)) break;
            fprintf(stderr, "stroke interrupted, redrawing\n");
        }
        start = i = end;
        i--;
    }
}

/* a baked file, shifted by (dx, dy) display px; missing files are skipped. Returns 1 if it existed. */
static int stroke_file(const char *name, int eraser, int dx, int dy, int xmax_px) {
    size_t len; unsigned char *blob = load(name, &len);
    if (!blob) return 0;
    play_blob(blob, len, eraser, units_x(dx), units_y(dy), xmax_px < 0 ? -1 : units_x(xmax_px));
    free(blob);
    return 1;
}

static void hover(long us) {   /* pen in proximity, not touching, for `us` microseconds */
    struct ev in[3] = {E(EV_KEY, BTN_TOOL_PEN, 1), E(EV_ABS, ABS_DISTANCE, 20), E(EV_SYN, 0, 0)};
    write_frame(in, 3);
    for (long t = 0; t < us; t += FRAME_US) {
        struct ev e[2] = {E(EV_ABS, ABS_DISTANCE, 20 + (int)((t / FRAME_US) & 1)), E(EV_SYN, 0, 0)};
        write_frame(e, 2);
    }
    struct ev out[3] = {E(EV_ABS, ABS_DISTANCE, 60), E(EV_KEY, BTN_TOOL_PEN, 0), E(EV_SYN, 0, 0)};
    write_frame(out, 3);
    last_tool = 1;
}

static int doc_open(const char *doc) {   /* xochitl keeps the open document's uuid in its config, empty on the home screen */
    FILE *f = fopen(CONF, "r");
    if (!f) return 0;
    char line[512]; int open = 0;
    while (fgets(line, sizeof line, f))
        if (!strncmp(line, "LastOpen=", 9)) { open = strstr(line, doc) != NULL; break; }
    fclose(f);
    return open;
}

static long mtime(const char *path) { struct stat st; return stat(path, &st) ? 0 : (long)st.st_mtime; }

/* "key=value" lookup in a small text file; returns 1 and copies the value when found */
static int read_kv(const char *name, const char *key, char *out, size_t cap) {
    char path[1100]; snprintf(path, sizeof path, "%s/%s", dir, name);
    FILE *f = fopen(path, "r"); if (!f) return 0;
    char line[1024]; size_t kl = strlen(key); int found = 0;
    while (fgets(line, sizeof line, f))
        if (!strncmp(line, key, kl) && line[kl] == '=') { line[strcspn(line, "\r\n")] = 0; snprintf(out, cap, "%s", line + kl + 1); found = 1; break; }
    fclose(f);
    return found;
}

static void use_pc_timezone(void) {   /* the tablet runs on UTC; the installer ships the PC's zoneinfo file */
    char path[600]; snprintf(path, sizeof path, "%s/localtime", dir);
    if (!access(path, R_OK)) { char tz[700]; snprintf(tz, sizeof tz, ":%s", path); setenv("TZ", tz, 1); tzset(); }
}

static int open_device(void) {
    fd = open(DEV, O_RDWR | O_NONBLOCK);
    if (fd < 0) { perror(DEV); return 0; }
    lift_everything();
    return 1;
}

static void close_device(void) { if (fd >= 0) { lift_everything(); close(fd); fd = -1; } }

/* our strokes must keep showing up in the page's autosave; if they stop while we are awake, the
 * document is not what is on screen. A long gap between checks was a suspend and does not count. */
struct ink { const char *rm; long seen, awake_s; long long last; };
static void ink_reset(struct ink *w, const char *rm) { w->rm = rm; w->seen = mtime(rm); w->awake_s = 0; w->last = now_us(); }
static int ink_lost(struct ink *w) {
    long m = mtime(w->rm);
    long long now = now_us(), gap = (now - w->last) / 1000000;
    w->last = now;
    if (m != w->seen) { w->seen = m; w->awake_s = 0; } else w->awake_s += gap < 10 ? gap : 0;
    if (w->awake_s <= INK_TIMEOUT_S) return 0;
    fprintf(stderr, "no autosave of our strokes for %ld s: page not on screen, stopping until it is reopened\n", w->awake_s);
    return 1;
}

static void parse_dev_args(int argc, char **argv) {   /* [xochitl.conf] [event device] for dry runs off the tablet */
    if (argc > 2) CONF = argv[2];
    if (argc > 3) DEV = argv[3];
}
