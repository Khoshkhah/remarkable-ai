/* rmclock: the 7-segment clock, drawn on the reMarkable by the tablet itself (no PC attached).
 *
 * `rm-ai clock --install` bakes every stroke of the approved clock into raw Linux input events (the
 * very bytes the Python VirtualStylus would write) and puts them next to this program:
 *   d<slot><seg>.bin / e<slot><seg>.bin   pen / eraser strokes for segment <seg> of digit <slot>
 *   colon<i>.bin, frame.bin               the colon dots and the frame (pen)
 *   localtime                             the PC's zoneinfo file (the tablet runs on UTC)
 *   config                                doc=<uuid>  rm=<page .rm path>  slots=<n>  fmt=<strftime>  interval=<s>
 * The replay keeps the pacing measured on the device (see rm_ai.py): one frame per 3 ms, a pause
 * after every eraser stroke and on pen<->eraser switches, a hover after the start-up erase.
 * It draws only while xochitl reports the clock document open (LastOpen in its config) and its
 * strokes keep landing in that page's .rm file, and it yields to the real pen: any digitizer event
 * that is not an echo of our own aborts the stroke, which is redrawn once the pen is gone.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <unistd.h>
#include <fcntl.h>
#include <time.h>
#include <sys/time.h>
#include <sys/stat.h>

#define EV_SYN 0
#define EV_KEY 1
#define EV_ABS 3
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

struct ev { uint32_t sec, usec; uint16_t type, code; int32_t value; };   /* 16 bytes, the device's ABI */

static const char *SEGS = "ABCDEFG";
static const char *DIGIT_SEGS[] = {"ABCDEF", "BC", "ABGED", "ABGCD", "FGBC", "AFGCD", "AFEDCG", "ABC", "ABCDEFG", "ABCDFG"};
static const char *CONF = "/home/root/.config/remarkable/xochitl.conf", *DEV = "/dev/input/event1";

static char dir[512], doc[64], rmfile[600], fmt[32] = "%H%M%S";
static long interval = 2;
static int slots = 6, fd = -1;
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

/* replay one baked stroke frame by frame; 0 if the real pen interrupted it */
static int play(const unsigned char *blob, size_t len, int eraser) {
    const struct ev *ev = (const struct ev *)blob;
    size_t n = len / sizeof *ev, start = 0;
    int tool = eraser ? 2 : 1;
    if (last_tool && tool != last_tool) usleep(TOOL_SETTLE_US);
    last_tool = tool;
    for (size_t i = 0; i < n; i++) {
        if (ev[i].type != EV_SYN) continue;
        if (pen_near()) { lift(eraser ? BTN_TOOL_RUBBER : BTN_TOOL_PEN); return 0; }
        write_frame(ev + start, (int)(i - start + 1));
        start = i + 1;
    }
    if (eraser) usleep(ERASE_SETTLE_US);
    return 1;
}

static unsigned char *load(const char *name, size_t *len) {
    char path[1100]; snprintf(path, sizeof path, "%s/%s", dir, name);
    FILE *f = fopen(path, "rb"); if (!f) { *len = 0; return NULL; }
    unsigned char *buf = malloc(MAXBLOB); *len = fread(buf, 1, MAXBLOB, f); fclose(f); return buf;
}

/* a stroke is retried until it completes with the real pen away; missing files are skipped */
static int stroke(const char *name, int eraser) {
    size_t len; unsigned char *blob = load(name, &len);
    if (!blob) return 0;
    while (1) {
        while (pen_near()) nap(100000);
        if (play(blob, len, eraser)) break;
        fprintf(stderr, "stroke interrupted, redrawing\n");
    }
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

static int clock_doc_open(void) {   /* xochitl keeps the open document's uuid in its config */
    FILE *f = fopen(CONF, "r");
    if (!f) return 0;
    char line[512]; int open = 0;
    while (fgets(line, sizeof line, f))
        if (!strncmp(line, "LastOpen=", 9)) { open = strstr(line, doc) != NULL; break; }
    fclose(f);
    return open;
}

static long mtime(const char *path) { struct stat st; return stat(path, &st) ? 0 : (long)st.st_mtime; }

static void read_config(void) {
    char path[600]; snprintf(path, sizeof path, "%s/config", dir);
    FILE *f = fopen(path, "r"); if (!f) { perror("config"); exit(1); }
    char line[700];
    while (fgets(line, sizeof line, f)) {
        line[strcspn(line, "\r\n")] = 0;
        if (!strncmp(line, "doc=", 4)) strncpy(doc, line + 4, sizeof doc - 1);
        else if (!strncmp(line, "rm=", 3)) strncpy(rmfile, line + 3, sizeof rmfile - 1);
        else if (!strncmp(line, "fmt=", 4)) strncpy(fmt, line + 4, sizeof fmt - 1);
        else if (!strncmp(line, "interval=", 9)) interval = atol(line + 9);
        else if (!strncmp(line, "slots=", 6)) slots = atoi(line + 6);
    }
    fclose(f);
    if (interval < 1) interval = 1;
    snprintf(path, sizeof path, "%s/localtime", dir);
    if (!access(path, R_OK)) { char tz[700]; snprintf(tz, sizeof tz, ":%s", path); setenv("TZ", tz, 1); tzset(); }
}

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: rmclock <dir> [xochitl.conf] [event device]   (the last two for dry runs off the tablet)\n"); return 1; }
    strncpy(dir, argv[1], sizeof dir - 1);
    if (argc > 2) CONF = argv[2];
    if (argc > 3) DEV = argv[3];
    read_config();
    setvbuf(stderr, NULL, _IOLBF, 0);
    char shown[8] = "", name[32];
    int active = 0, ink_lost = 0;
    long rm_seen = 0, awake_s = 0;
    long long last_loop = now_us();
    while (1) {
        int is_open = clock_doc_open();
        if (!is_open) ink_lost = 0;                 /* a fresh open of the document is a fresh start */
        if (!is_open || ink_lost) {
            if (active) { fprintf(stderr, "clock document closed, stopping\n"); lift_everything(); close(fd); fd = -1; active = 0; }
            sleep(2);
            continue;
        }
        if (!active) {
            fprintf(stderr, "clock document open, starting\n");
            fd = open(DEV, O_RDWR | O_NONBLOCK);
            if (fd < 0) { perror(DEV); sleep(5); continue; }
            lift_everything();
            for (int s = 0; s < slots; s++)               /* erase whatever an earlier run left on the segment lines */
                for (int g = 0; g < 7; g++) { snprintf(name, sizeof name, "e%d%c.bin", s, SEGS[g]); stroke(name, 1); }
            hover(START_SETTLE_US);
            for (int c = 0; c < 8; c++) { snprintf(name, sizeof name, "colon%d.bin", c); if (!stroke(name, 0)) break; }
            stroke("frame.bin", 0);
            shown[0] = 0;
            active = 1;
            rm_seen = mtime(rmfile); awake_s = 0; last_loop = now_us();
        }
        /* the time at the last interval boundary, e.g. :00 :02 :04 */
        time_t t = (time_t)((time(NULL) / interval) * interval);
        struct tm lt; localtime_r(&t, &lt);
        char want[8]; strftime(want, sizeof want, fmt, &lt);
        if (strcmp(want, shown)) {
            for (int s = 0; s < slots; s++) {              /* all erasing first ... */
                if (shown[0] && shown[s] == want[s]) continue;
                const char *old = shown[0] ? DIGIT_SEGS[shown[s] - '0'] : "";
                const char *new = DIGIT_SEGS[want[s] - '0'];
                for (const char *p = old; *p; p++) if (!strchr(new, *p)) { snprintf(name, sizeof name, "e%d%c.bin", s, *p); stroke(name, 1); }
            }
            for (int s = 0; s < slots; s++) {              /* ... then all drawing: one eraser->pen switch per tick */
                if (shown[0] && shown[s] == want[s]) continue;
                const char *old = shown[0] ? DIGIT_SEGS[shown[s] - '0'] : "";
                const char *new = DIGIT_SEGS[want[s] - '0'];
                for (const char *p = new; *p; p++) if (!strchr(old, *p)) { snprintf(name, sizeof name, "d%d%c.bin", s, *p); stroke(name, 0); }
            }
            if (!shown[0]) fprintf(stderr, "showing %s\n", want);
            strcpy(shown, want);
        }
        /* our strokes must keep showing up in the page's autosave; if they stop while we are awake, the
         * document is not what is on screen (xochitl may leave LastOpen set on the home screen) */
        long m = mtime(rmfile);
        long long now = now_us(), gap = (now - last_loop) / 1000000;
        last_loop = now;
        if (m != rm_seen) { rm_seen = m; awake_s = 0; } else awake_s += gap < 10 ? gap : 0;   /* a long gap was a suspend */
        if (awake_s > INK_TIMEOUT_S) { fprintf(stderr, "no autosave of the clock for %ld s: page not on screen, stopping until it is reopened\n", awake_s); ink_lost = 1; continue; }
        long long us = interval * 1000000LL - now_us() % (interval * 1000000LL);
        nap(us);
    }
}
