/* rmvocab: a printer for the reMarkable. The PC bakes whatever it wants written on the Vocabulary page
 * (an explanation of a word, in pen strokes; a page wipe, in eraser strokes) as files in `queue/`, and
 * this program draws them in name order whenever that page is open, then deletes them. Same rules as
 * the clock and the dashboard (stylus.h): only while the tablet's app shows the page, yielding to the
 * real pen, one stroke at a time.
 *   config: doc=<uuid>  rm=<page .rm path>
 *   queue/<name>.bin: pen and eraser strokes, each stroke complete (tool in ... tool out)
 */
#include "stylus.h"
#include <dirent.h>

static char doc[64], rmfile[600];

static int cmp(const void *a, const void *b) { return strcmp(*(char *const *)a, *(char *const *)b); }

/* the strokes of a blob may use either tool: each is played with its own */
static void play_mixed(const unsigned char *blob, size_t len) {
    const struct ev *ev = (const struct ev *)blob;
    size_t n = len / sizeof *ev, start = 0;
    int tool = 0;
    for (size_t i = 0; i < n; i++) {
        if (ev[i].type == EV_KEY && (ev[i].code == BTN_TOOL_PEN || ev[i].code == BTN_TOOL_RUBBER) && ev[i].value == 1) { start = i; tool = ev[i].code; }
        if (!(tool && ev[i].type == EV_KEY && ev[i].code == tool && ev[i].value == 0)) continue;
        size_t end = i + 1;
        while (end < n && ev[end].type != EV_SYN) end++;
        if (end < n) end++;
        if (page_lost || !page_on_screen()) { page_lost = 1; return; }
        while (1) {
            while (pen_near()) nap(100000);
            if (play_stroke(ev + start, end - start, tool == BTN_TOOL_RUBBER, 0, 0, -1)) break;
            fprintf(stderr, "stroke interrupted, redrawing\n");
        }
        i = end - 1; tool = 0;
    }
}

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: rmvocab <dir> [xochitl.conf] [event device]\n"); return 1; }
    strncpy(dir, argv[1], sizeof dir - 1);
    parse_dev_args(argc, argv);
    if (!read_kv("config", "doc", doc, sizeof doc)) { fprintf(stderr, "config: no doc=\n"); return 1; }
    read_kv("config", "rm", rmfile, sizeof rmfile);
    use_pc_timezone();
    setvbuf(stderr, NULL, _IOLBF, 0);
    journal_start(doc);
    char qdir[600]; snprintf(qdir, sizeof qdir, "%s/queue", dir);
    int active = 0;
    while (1) {
        int is_open = page_on_screen();
        if (!is_open) {
            if (active) { fprintf(stderr, "vocabulary page closed\n"); close_device(); active = 0; give_back_the_pen(doc); }
            sleep(2);
            continue;
        }
        if (!active) { fprintf(stderr, "vocabulary page open\n"); if (!open_device()) { sleep(5); continue; } active = 1; }
        page_lost = 0;
        DIR *d = opendir(qdir); char *names[256]; int nn = 0;
        if (d) { struct dirent *e; while ((e = readdir(d)) && nn < 256) if (strstr(e->d_name, ".bin")) names[nn++] = strdup(e->d_name); closedir(d); }
        qsort(names, nn, sizeof *names, cmp);
        for (int i = 0; i < nn; i++) {
            char name[700]; snprintf(name, sizeof name, "queue/%s", names[i]);
            size_t len; unsigned char *blob = load(name, &len);
            if (blob) {
                long long t0 = now_us();
                play_mixed(blob, len);
                free(blob);
                if (page_lost) { fprintf(stderr, "page left mid-item, %s stays queued\n", names[i]); break; }
                char path[1300]; snprintf(path, sizeof path, "%s/%s", dir, name); unlink(path);
                fprintf(stderr, "wrote %s (%.0f s)\n", names[i], (now_us() - t0) / 1e6);
                hover(1000000);
            }
        }
        for (int i = 0; i < nn; i++) free(names[i]);
        if (page_lost) { close_device(); active = 0; continue; }
        nap(2000000);
    }
}
