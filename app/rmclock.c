/* rmclock: the 7-segment clock, drawn on the reMarkable by the tablet itself (no PC attached).
 *
 * `rm-ai clock --install` bakes every stroke of the approved clock (see stylus.h for the format):
 *   d<slot><seg>.bin / e<slot><seg>.bin   pen / eraser strokes for segment <seg> of digit <slot>
 *   colon<i>.bin, frame.bin               the colon dots and the frame (pen)
 *   localtime                             the PC's zoneinfo file (the tablet runs on UTC)
 *   config                                doc=<uuid>  rm=<page .rm path>  slots=<n>  fmt=<strftime>  interval=<s>
 * It draws only while xochitl reports the clock document open and its strokes keep landing in
 * that page's .rm file.
 */
#include "stylus.h"

static const char *SEGS = "ABCDEFG";
static const char *DIGIT_SEGS[] = {"ABCDEF", "BC", "ABGED", "ABGCD", "FGBC", "AFGCD", "AFEDCG", "ABC", "ABCDEFG", "ABCDFG"};
static char doc[64], rmfile[600], fmt[32] = "%H%M%S", tmp[32];
static long interval = 2;
static int slots = 6;

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: rmclock <dir> [xochitl.conf] [event device]   (the last two for dry runs off the tablet)\n"); return 1; }
    strncpy(dir, argv[1], sizeof dir - 1);
    parse_dev_args(argc, argv);
    if (!read_kv("config", "doc", doc, sizeof doc)) { fprintf(stderr, "config: no doc=\n"); return 1; }
    read_kv("config", "rm", rmfile, sizeof rmfile);
    read_kv("config", "fmt", fmt, sizeof fmt);
    if (read_kv("config", "interval", tmp, sizeof tmp)) interval = atol(tmp);
    if (read_kv("config", "slots", tmp, sizeof tmp)) slots = atoi(tmp);
    if (interval < 1) interval = 1;
    use_pc_timezone();
    setvbuf(stderr, NULL, _IOLBF, 0);
    journal_start(doc);
    char shown[8] = "", name[32];
    int active = 0, lost = 0;
    struct ink ink = {0};
    while (1) {
        int is_open = page_on_screen();
        if (!is_open) lost = 0;                  /* a fresh open of the document is a fresh start */
        if (!is_open || lost) {
            if (active) { fprintf(stderr, "clock document closed, stopping\n"); close_device(); active = 0; }
            sleep(2);
            continue;
        }
        if (!active) {
            fprintf(stderr, "clock document open, starting\n");
            if (!open_device()) { sleep(5); continue; }
            for (int s = 0; s < slots; s++)               /* erase whatever an earlier run left on the segment lines */
                for (int g = 0; g < 7; g++) { snprintf(name, sizeof name, "e%d%c.bin", s, SEGS[g]); stroke_file(name, 1, 0, 0, -1); }
            hover(START_SETTLE_US);
            for (int c = 0; c < 8; c++) { snprintf(name, sizeof name, "colon%d.bin", c); if (!stroke_file(name, 0, 0, 0, -1)) break; }
            stroke_file("frame.bin", 0, 0, 0, -1);
            shown[0] = 0;
            active = 1;
            ink_reset(&ink, rmfile);
            if (page_lost) { page_lost = 0; lost = 1; continue; }
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
                for (const char *p = old; *p; p++) if (!strchr(new, *p)) { snprintf(name, sizeof name, "e%d%c.bin", s, *p); stroke_file(name, 1, 0, 0, -1); }
            }
            for (int s = 0; s < slots; s++) {              /* ... then all drawing: one eraser->pen switch per tick */
                if (shown[0] && shown[s] == want[s]) continue;
                const char *old = shown[0] ? DIGIT_SEGS[shown[s] - '0'] : "";
                const char *new = DIGIT_SEGS[want[s] - '0'];
                for (const char *p = new; *p; p++) if (!strchr(old, *p)) { snprintf(name, sizeof name, "d%d%c.bin", s, *p); stroke_file(name, 0, 0, 0, -1); }
            }
            if (page_lost) { page_lost = 0; lost = 1; continue; }   /* redrawn from scratch when the page is back */
            if (!shown[0]) fprintf(stderr, "showing %s\n", want);
            strcpy(shown, want);
        }
        if (page_lost) { page_lost = 0; lost = 1; continue; }
        if (ink_lost(&ink)) { lost = 1; continue; }
        long long us = interval * 1000000LL - now_us() % (interval * 1000000LL);
        nap(us);
    }
}
