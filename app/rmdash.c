/* rmdash: the dashboard page, kept current on the reMarkable by the tablet itself.
 *
 * `rm-ai dashboard --install` pushes the page (printed labels only) and bakes, next to this program:
 *   g<size>_<code>.bin     pen strokes of one glyph, baked with the text origin at `base` (see layout)
 *   sweep_<zone>.bin       eraser serpentine over one zone;  bar<i>.bin  a full usage bar (truncated here)
 *   ring.bin               the ring around today's calendar day, centered on `base`
 *   layout, glyphs         zones/text positions and per-glyph advance widths (written by rm_ai.py)
 *   config                 doc= rm= lat= lon= city= minutes=
 *   usage                  the three usage rows: written by the PC every 15 min, or here when a
 *   token                  Claude login of the tablet's own (access= refresh= expires=) exists
 * Every minute the clock is redrawn; date, calendar, weather and usage rows only when they change.
 * The tablet fetches weather (Open-Meteo) and, with a token, Claude usage through openssl itself.
 */
#include "stylus.h"

#define NZ 8
static const char *ZONES[NZ] = {"clock", "date", "caltitle", "cal", "row0", "row1", "row2", "weather"};
static char doc[64], rmfile[600], pdf[600], city[64], tmpv[128];
static double lat = 0, lon = 0;
static long minutes = 15;
static int base_x = 300, base_y = 300;

struct text { char name[16]; int size, x, y; };
static struct text texts[40]; static int ntexts = 0;
static struct { int x0, x1, y; } bars[3];
static struct { int x0, y0, col, row; } cal;
static struct { int size; double adv[256]; } gl[8]; static int ngl = 0;
static int zone_on[NZ];   /* a zone is drawn only if its sweep was baked: the layout decides what the pen owns */

static struct text *T(const char *name) { for (int i = 0; i < ntexts; i++) if (!strcmp(texts[i].name, name)) return &texts[i]; return NULL; }

static void read_layout(void) {
    char path[600]; snprintf(path, sizeof path, "%s/layout", dir);
    FILE *f = fopen(path, "r"); if (!f) { perror("layout"); exit(1); }
    char line[256], kind[16], name[16];
    while (fgets(line, sizeof line, f)) {
        if (sscanf(line, "text %15s %d %d %d", name, &texts[ntexts].size, &texts[ntexts].x, &texts[ntexts].y) == 4 && ntexts < 40) { strcpy(texts[ntexts].name, name); ntexts++; }
        else if (sscanf(line, "%15s", kind) == 1 && !strcmp(kind, "bar")) {
            int i, x0, x1, y;
            if (sscanf(line, "bar %d %d %d %d", &i, &x0, &x1, &y) == 4 && i >= 0 && i < 3) { bars[i].x0 = x0; bars[i].x1 = x1; bars[i].y = y; }
        }
        else if (!strcmp(kind, "cal")) sscanf(line, "cal %d %d %d %d", &cal.x0, &cal.y0, &cal.col, &cal.row);
        else if (!strcmp(kind, "base")) sscanf(line, "base %d %d", &base_x, &base_y);
    }
    fclose(f);
    snprintf(path, sizeof path, "%s/glyphs", dir);
    f = fopen(path, "r"); if (!f) { perror("glyphs"); exit(1); }
    int size, code; double adv;
    while (fscanf(f, "%d %d %lf", &size, &code, &adv) == 3) {
        int i; for (i = 0; i < ngl && gl[i].size != size; i++);
        if (i == ngl) { if (ngl == 8) continue; gl[ngl++].size = size; }
        if (code >= 0 && code < 256) gl[i].adv[code] = adv;
    }
    fclose(f);
}

static double text_width(int size, const char *s) {
    int i; for (i = 0; i < ngl && gl[i].size != size; i++);
    double w = 0; for (const unsigned char *p = (const unsigned char *)s; *p; p++) w += gl[i].adv[*p];
    return w;
}

/* draw `s` in the baked glyphs of `size` with its text origin at (x, y) display px; with `eraser` the
 * glyphs' eraser twins run along the same strokes and take the text out again */
static void draw_text(int size, double x, int y, const char *s, int eraser) {
    int i; for (i = 0; i < ngl && gl[i].size != size; i++);
    if (i == ngl) { fprintf(stderr, "no glyphs of size %d\n", size); return; }
    char name[32];
    for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
        if (*p != ' ') { snprintf(name, sizeof name, "%c%d_%d.bin", eraser ? 'e' : 'g', size, *p); stroke_file(name, eraser, (int)lround(x) - base_x, y - base_y, -1); }
        x += gl[i].adv[*p];
    }
}

static void draw_at(const char *tname, const char *s, int dy, int eraser) { struct text *t = T(tname); if (t) draw_text(t->size, t->x, t->y + dy, s, eraser); }

/* ---------- data: HTTPS through openssl, tiny JSON scanning ---------- */

static int https(const char *host, const char *request, char *out, size_t cap) {
    const char *fx = getenv("RM_FIXTURES");   /* dry runs off the tablet: canned responses */
    if (fx) {
        char p[600]; snprintf(p, sizeof p, "%s/%s", fx, strstr(host, "meteo") ? "weather.json" : strstr(host, "console") ? "token.json" : "usage.json");
        FILE *f = fopen(p, "r"); if (!f) return -1;
        size_t n = fread(out, 1, cap - 1, f); out[n] = 0; fclose(f); return 200;
    }
    char req[600]; snprintf(req, sizeof req, "%s/req", dir);
    FILE *f = fopen(req, "w"); if (!f) return -1;
    chmod(req, 0600); fputs(request, f); fclose(f);
    char cmd[1200];
    snprintf(cmd, sizeof cmd, "openssl s_client -quiet -ign_eof -connect %s:443 -servername %s -verify_return_error -CApath /etc/ssl/certs < %s 2>/dev/null & p=$!; "
             "(sleep 25; kill $p 2>/dev/null) >/dev/null 2>&1 & w=$!; wait $p; kill $w 2>/dev/null", host, host, req);
    FILE *pp = popen(cmd, "r"); if (!pp) return -1;
    size_t n = fread(out, 1, cap - 1, pp); out[n] = 0; pclose(pp); unlink(req);
    if (n < 12 || strncmp(out, "HTTP/", 5)) return -1;
    int status = atoi(out + 9);
    char *body = strstr(out, "\r\n\r\n");
    if (!body) return -1;
    memmove(out, body + 4, strlen(body + 4) + 1);
    return status;
}

static const char *jkey(const char *s, const char *key) {   /* after `"key":` (first occurrence from s) */
    char k[64]; snprintf(k, sizeof k, "\"%s\":", key);
    const char *p = strstr(s, k); return p ? p + strlen(k) : NULL;
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

static const char *WMO(int code) {   /* WEATHER_TEXT in rm_ai.py */
    switch (code) {
    case 0: return "Clear"; case 1: return "Mostly clear"; case 2: return "Partly cloudy"; case 3: return "Overcast";
    case 45: return "Fog"; case 48: return "Rime fog"; case 51: return "Light drizzle"; case 53: return "Drizzle"; case 55: return "Heavy drizzle";
    case 56: case 57: return "Freezing drizzle"; case 61: return "Light rain"; case 63: return "Rain"; case 65: return "Heavy rain";
    case 66: case 67: return "Freezing rain"; case 71: return "Light snow"; case 73: return "Snow"; case 75: return "Heavy snow"; case 77: return "Snow grains";
    case 80: case 81: return "Showers"; case 82: return "Heavy showers"; case 85: case 86: return "Snow showers"; case 95: return "Thunderstorm";
    case 96: case 99: return "Thunderstorm, hail"; default: return "?";
    }
}

static struct { int ok; double temp, feels, wind; int code, n; char day[6][12], rise[6][12], set[6][12]; double dcode[6], dmax[6], dmin[6], dpop[6]; time_t at; } wx;
static char big[65536];

static void fetch_weather(void) {
    if (lat == 0 && lon == 0) return;
    char req[700];
    snprintf(req, sizeof req, "GET /v1/forecast?latitude=%.4f&longitude=%.4f&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m"
             "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset&timezone=auto&forecast_days=6&wind_speed_unit=ms HTTP/1.0\r\n"
             "Host: api.open-meteo.com\r\nUser-Agent: remarkable-ai/0.1\r\n\r\n", lat, lon);
    int st = https("api.open-meteo.com", req, big, sizeof big);
    const char *cur = st == 200 ? strstr(big, "\"current\":{") : NULL, *day = st == 200 ? strstr(big, "\"daily\":{") : NULL;
    if (!cur || !day) { fprintf(stderr, "weather: HTTP %d\n", st); return; }
    wx.temp = jnum(cur, "temperature_2m", 0); wx.feels = jnum(cur, "apparent_temperature", 0);
    wx.code = (int)jnum(cur, "weather_code", -1); wx.wind = jnum(cur, "wind_speed_10m", 0);
    wx.n = jarr_str(day, "time", wx.day, 6);
    jarr(day, "weather_code", wx.dcode, 6); jarr(day, "temperature_2m_max", wx.dmax, 6); jarr(day, "temperature_2m_min", wx.dmin, 6);
    if (!jarr(day, "precipitation_probability_max", wx.dpop, 6)) for (int i = 0; i < 6; i++) wx.dpop[i] = -999;
    jarr_str(day, "sunrise", wx.rise, 6); jarr_str(day, "sunset", wx.set, 6);
    wx.ok = wx.n > 0; wx.at = time(NULL);
    fprintf(stderr, "weather: %.0f%c %s, %d days\n", wx.temp, 0xB0, WMO(wx.code), wx.n);
}

static time_t parse_iso(const char *s) {   /* 2026-09-08T21:59:59.530944+00:00 or ...Z -> epoch */
    struct tm t = {0}; int off = 0, oh, om; char sign;
    if (sscanf(s, "%d-%d-%dT%d:%d:%d", &t.tm_year, &t.tm_mon, &t.tm_mday, &t.tm_hour, &t.tm_min, &t.tm_sec) != 6) return 0;
    t.tm_year -= 1900; t.tm_mon -= 1;
    const char *z = s + 19; while (*z && *z != '+' && *z != '-' && *z != 'Z') z++;
    if (sscanf(z, "%c%d:%d", &sign, &oh, &om) == 3) off = (oh * 3600 + om * 60) * (sign == '-' ? -1 : 1);
    return timegm(&t) - off;
}

static int token_refresh(void) {
    char refresh[600]; if (!read_kv("token", "refresh", refresh, sizeof refresh)) return 0;
    char body[800], req[1200];
    snprintf(body, sizeof body, "{\"grant_type\":\"refresh_token\",\"refresh_token\":\"%s\",\"client_id\":\"9d1c250a-e61b-44d9-88ed-5944d1962f5e\"}", refresh);
    snprintf(req, sizeof req, "POST /v1/oauth/token HTTP/1.0\r\nHost: console.anthropic.com\r\nContent-Type: application/json\r\nAccept: application/json\r\n"
             "User-Agent: remarkable-ai/0.1\r\nContent-Length: %zu\r\n\r\n%s", strlen(body), body);
    int st = https("console.anthropic.com", req, big, sizeof big);
    char access[600], newref[600]; double exp = jnum(big, "expires_in", 0);
    if (st != 200 || !jstr(big, "access_token", access, sizeof access)) { fprintf(stderr, "token renewal failed: HTTP %d\n", st); return 0; }
    if (!jstr(big, "refresh_token", newref, sizeof newref)) strcpy(newref, refresh);
    char path[600], tmp[600]; snprintf(path, sizeof path, "%s/token", dir); snprintf(tmp, sizeof tmp, "%s/token.tmp", dir);
    FILE *f = fopen(tmp, "w"); if (!f) return 0;
    chmod(tmp, 0600); fprintf(f, "access=%s\nrefresh=%s\nexpires=%ld\n", access, newref, (long)(time(NULL) + (long)exp)); fclose(f);
    rename(tmp, path);
    fprintf(stderr, "token renewed, valid %.1f h\n", exp / 3600);
    return 1;
}

/* Claude usage with the tablet's own login -> the `usage` file (same format the PC writes) */
static void fetch_usage(void) {
    char access[600], expv[32];
    if (!read_kv("token", "access", access, sizeof access)) return;
    if (read_kv("token", "expires", expv, sizeof expv) && atol(expv) - time(NULL) < 600) { if (!token_refresh()) return; read_kv("token", "access", access, sizeof access); }
    char req[1000];
    for (int attempt = 0; attempt < 2; attempt++) {
        snprintf(req, sizeof req, "GET /api/oauth/usage HTTP/1.0\r\nHost: api.anthropic.com\r\nAuthorization: Bearer %s\r\nanthropic-beta: oauth-2025-04-20\r\n"
                 "anthropic-version: 2023-06-01\r\nUser-Agent: remarkable-ai/0.1\r\n\r\n", access);
        int st = https("api.anthropic.com", req, big, sizeof big);
        if (st == 401 && attempt == 0) { if (!token_refresh()) return; read_kv("token", "access", access, sizeof access); continue; }
        if (st != 200) { fprintf(stderr, "usage: HTTP %d\n", st); return; }
        break;
    }
    char path[600], tmp[600]; snprintf(path, sizeof path, "%s/usage", dir); snprintf(tmp, sizeof tmp, "%s/usage.tmp", dir);
    FILE *f = fopen(tmp, "w"); if (!f) return;
    fprintf(f, "fetched=%ld\n", (long)time(NULL));
    int n = 0;
    for (const char *p = strstr(big, "\"kind\":\""); p && n < 3; p = strstr(p + 1, "\"kind\":\"")) {
        char kind[32], model[32], label[40];
        jstr(p, "kind", kind, sizeof kind);
        const char *next = strstr(p + 1, "\"kind\":\"");
        const char *pc = jkey(p, "percent"); if (!pc || (next && pc > next) || !(*pc == '-' || (*pc >= '0' && *pc <= '9'))) continue;
        const char *rs = jkey(p, "resets_at"); time_t reset = rs && (!next || rs < next) && *rs == '"' ? parse_iso(rs + 1) : 0;
        if (!strcmp(kind, "session")) strcpy(label, "Session");
        else if (!strcmp(kind, "weekly_all")) strcpy(label, "Week");
        else if (!strcmp(kind, "weekly_scoped")) { const char *m = jkey(p, "display_name"); snprintf(label, sizeof label, "Week %s", m && (!next || m < next) && jstr(p, "display_name", model, sizeof model) ? model : "model"); }
        else snprintf(label, sizeof label, "%.12s", kind);
        fprintf(f, "label%d=%s\npct%d=%d\nreset%d=%ld\n", n, label, n, (int)lround(atof(pc)), n, (long)reset); n++;
    }
    fprintf(f, "n=%d\n", n); fclose(f); rename(tmp, path);
    fprintf(stderr, "usage: %d rows\n", n);
}

/* ---------- what the page should show ---------- */

static void upper(char *s) { for (; *s; s++) if (*s >= 'a' && *s <= 'z') *s -= 32; }

static void want_all(char want[NZ][512], struct tm *lt) {
    for (int z = 0; z < NZ; z++) want[z][0] = 0;
    strftime(want[0], 512, "%H:%M", lt);
    strftime(want[1], 512, "%A, %B %d, %Y", lt); upper(want[1]);
    strftime(want[2], 512, "%B %Y", lt); upper(want[2]);
    strftime(want[3], 512, "%Y-%m-%d", lt);
    for (int i = 0; i < 3; i++) {
        char k[8], pct[16], reset[32]; snprintf(k, sizeof k, "pct%d", i);
        if (!read_kv("usage", k, pct, sizeof pct)) { want[4 + i][0] = 0; continue; }
        (void)reset;
        snprintf(want[4 + i], 512, "%d", atoi(pct));
    }
    for (int z = 0; z < NZ; z++) if (!zone_on[z]) want[z][0] = 0;
    if (!wx.ok || !zone_on[7]) { want[7][0] = 0; return; }
    int n = 0;
    n += snprintf(want[7] + n, 512 - n, "%.0f\xB0|%s|H %.0f\xB0  L %.0f\xB0", wx.temp, WMO(wx.code), wx.dmax[0], wx.dmin[0]);
    if (wx.dpop[0] > -999) n += snprintf(want[7] + n, 512 - n, "  rain %.0f%%", wx.dpop[0]);
    for (int i = 1; i < wx.n && i < 6; i++) {
        struct tm d = {0}; sscanf(wx.day[i], "%d-%d-%d", &d.tm_year, &d.tm_mon, &d.tm_mday); d.tm_year -= 1900; d.tm_mon -= 1; d.tm_hour = 12;
        time_t tt = mktime(&d); localtime_r(&tt, &d); char dow[8]; strftime(dow, sizeof dow, "%a", &d); upper(dow);
        n += snprintf(want[7] + n, 512 - n, "|%s|%s|%.0f\xB0/%.0f\xB0|", dow, WMO((int)wx.dcode[i]), wx.dmax[i], wx.dmin[i]);
        if (wx.dpop[i] > -999) n += snprintf(want[7] + n, 512 - n, "%.0f%%", wx.dpop[i]);
    }
}

/* draw a zone's content, or with `eraser` take exactly that content out again (the same strokes, run
 * with the eraser twins): this is how a zone is cleared before its new content, precise and quick */
static void draw_zone(int z, const char *want, struct tm *lt, int eraser) {
    char buf[512]; strncpy(buf, want, sizeof buf - 1); buf[511] = 0;
    if (z == 0) draw_at("clock", buf, 0, eraser);
    else if (z == 1) draw_at("date", buf, 0, eraser);
    else if (z == 2) draw_at("caltitle", buf, 0, eraser);
    else if (z == 3) {
        struct text *t = T("cal"); if (!t) return;
        struct tm first = *lt; first.tm_mday = 1; first.tm_hour = 12; time_t ft = mktime(&first); localtime_r(&ft, &first);
        int col = (first.tm_wday + 6) % 7, row = 0;                 /* Monday first */
        struct tm nxt = first; nxt.tm_mon++; time_t nt = mktime(&nxt); int ndays = (int)((nt - ft) / 86400 + 0.5);
        for (int d = 1; d <= ndays; d++) {
            char s[8]; snprintf(s, sizeof s, "%d", d);
            int cx = cal.x0 + col * cal.col, cy = cal.y0 + row * cal.row;
            draw_text(t->size, cx - text_width(t->size, s) / 2, cy - t->size * 0.55, s, eraser);
            if (d == lt->tm_mday) stroke_file(eraser ? "ering.bin" : "ring.bin", eraser, cx - base_x, cy - base_y, -1);
            if (++col == 7) { col = 0; row++; }
        }
    } else if (z >= 4 && z <= 6) {
        int i = z - 4; char *p = strtok(buf, "|"); if (!p) return;
        int pct = atoi(p);
        char name[20], s[8]; snprintf(name, sizeof name, "%sbar%d.bin", eraser ? "e" : "", i);
        if (pct > 0) stroke_file(name, eraser, 0, 0, bars[i].x0 + (bars[i].x1 - bars[i].x0) * (pct > 100 ? 100 : pct) / 100 + (eraser ? 8 : 0));
        snprintf(s, sizeof s, "%d", pct); snprintf(name, sizeof name, "pct%d", i); draw_at(name, s, 0, eraser);
    } else if (z == 7) {
        char *temp = strtok(buf, "|"), *text = strtok(NULL, "|"), *line = strtok(NULL, "|");
        if (temp) draw_at("temp", temp, 0, eraser);
        if (text) draw_at("wtext", text, 0, eraser);
        if (line) draw_at("wline", line, 0, eraser);
        for (int r = 0; r < 5; r++) {
            char *dow = strtok(NULL, "|"), *wt = strtok(NULL, "|"), *tt = strtok(NULL, "|"), *pop = strtok(NULL, "|");
            if (!dow || !wt || !tt) break;
            draw_at("fcdow", dow, 56 * r, eraser); draw_at("fctext", wt, 56 * r, eraser); draw_at("fctemp", tt, 56 * r, eraser);
            if (pop && *pop) draw_at("fcpop", pop, 56 * r, eraser);
        }
    }
}

/* ---------- the printed page: the PC's JPEG template for the day + the weather block as PDF text ---------- */
#define PT (72.0 / 226.0)   /* display px -> PDF points */
static char pdfbuf[1 << 16];
static size_t pdfn = 0;
static void pdf_esc(const char *s) {   /* PDF string with WinAnsi bytes (0xB0 degree, 0x95 bullet) */
    pdfn += snprintf(pdfbuf + pdfn, sizeof pdfbuf - pdfn, "(");
    for (const unsigned char *p = (const unsigned char *)s; *p && pdfn < sizeof pdfbuf - 8; p++) {
        if (*p == '(' || *p == ')' || *p == '\\') pdfn += snprintf(pdfbuf + pdfn, sizeof pdfbuf - pdfn, "\\%c", *p);
        else if (*p < 32 || *p > 126) pdfn += snprintf(pdfbuf + pdfn, sizeof pdfbuf - pdfn, "\\%03o", *p);
        else pdfbuf[pdfn++] = (char)*p;
    }
    pdfn += snprintf(pdfbuf + pdfn, sizeof pdfbuf - pdfn, ")");
}
/* text with its top-left at (x, y) display px in a font of `px` height, as PIL draws it (DejaVu ascent 0.93) */
static void pdf_text(int bold, double px, double x, double y, int gray, const char *s) {
    pdfn += snprintf(pdfbuf + pdfn, sizeof pdfbuf - pdfn, "BT /%s %.2f Tf %.3f g %.2f %.2f Td ", bold ? "FB" : "FR", px * PT, gray / 255.0, x * PT, (1872 - y - 0.93 * px) * PT);
    pdf_esc(s);
    pdfn += snprintf(pdfbuf + pdfn, sizeof pdfbuf - pdfn, " Tj ET\n");
}
static void pdf_line(double x0, double y0, double x1, double y1, double w, int gray) {
    pdfn += snprintf(pdfbuf + pdfn, sizeof pdfbuf - pdfn, "%.3f G %.2f w %.2f %.2f m %.2f %.2f l S\n", gray / 255.0, w * PT, x0 * PT, (1872 - y0) * PT, x1 * PT, (1872 - y1) * PT);
}
static const char *DOW3[] = {"SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"};
static void dow_of(const char *ymd, char *out) {
    struct tm d = {0}; sscanf(ymd, "%d-%d-%d", &d.tm_year, &d.tm_mon, &d.tm_mday); d.tm_year -= 1900; d.tm_mon -= 1; d.tm_hour = 12;
    time_t t = mktime(&d); localtime_r(&t, &d); strcpy(out, DOW3[d.tm_wday]);
}
/* the weather block exactly as render_dashboard_image() prints it for the sleep screen */
static void weather_block(void) {
    char s[200];
    if (!wx.ok) return;
    snprintf(s, sizeof s, "%.0f\xB0", wx.temp); pdf_text(1, 96, 630, 478, 0, s);
    pdf_text(1, 32, 840, 495, 0, WMO(wx.code));
    if (wx.dpop[0] > -999) snprintf(s, sizeof s, "feels %.0f\xB0  \x95  wind %.0f m/s  \x95  rain %.0f%%", wx.feels, wx.wind, wx.dpop[0]);
    else snprintf(s, sizeof s, "feels %.0f\xB0  \x95  wind %.0f m/s", wx.feels, wx.wind);
    pdf_text(0, 22, 840, 545, 0, s);
    if (wx.rise[0][0]) snprintf(s, sizeof s, "TODAY  H %.0f\xB0  L %.0f\xB0   \x95   sunrise %s   sunset %s", wx.dmax[0], wx.dmin[0], wx.rise[0], wx.set[0]);
    else snprintf(s, sizeof s, "TODAY  H %.0f\xB0  L %.0f\xB0", wx.dmax[0], wx.dmin[0]);
    pdf_text(1, 22, 630, 605, 0, s);
    int y = 655;
    for (int i = 1; i < wx.n && i < 6; i++, y += 42) {
        char dow[8]; dow_of(wx.day[i], dow);
        pdf_text(1, 22, 630, y, 0, dow);
        pdf_text(0, 22, 720, y, 0, WMO((int)wx.dcode[i]));
        snprintf(s, sizeof s, "%.0f\xB0 / %.0f\xB0", wx.dmax[i], wx.dmin[i]); pdf_text(0, 22, 1010, y, 0, s);
        if (wx.dpop[i] > -999) { snprintf(s, sizeof s, "%.0f%%", wx.dpop[i]); pdf_text(0, 22, 1210, y, 0, s); }
        pdf_line(630, y + 34, 1324, y + 34, 1, 220);
    }
}
/* the usage rows' reset times as in rm_ai.RESET_TEXT; also the string that tells when they changed */
static void reset_texts(char *joined, size_t cap) {
    joined[0] = 0;
    for (int i = 0; i < 3; i++) {
        char k[8], v[32], when[16] = ""; snprintf(k, sizeof k, "reset%d", i);
        if (read_kv("usage", k, v, sizeof v) && atol(v)) { time_t r = atol(v); struct tm rt; localtime_r(&r, &rt); strftime(when, sizeof when, "%a %H:%M", &rt); upper(when); }
        for (char *c = when; *c; c++) if (*c == ' ') *c = '_';   /* one token in the `printed` file */
        size_t n = strlen(joined); snprintf(joined + n, cap - n, "%s%s", i ? "|" : "", when);
    }
}
static void usage_block(void) {
    char joined[64]; reset_texts(joined, sizeof joined);
    char *p = joined;
    for (int i = 0; i < 3 && p; i++) {
        char *bar = strchr(p, '|'); if (bar) *bar = 0;
        int y = bars[i].y - 13;
        for (char *c = p; *c; c++) if (*c == '_') *c = ' ';
        if (*p) { pdf_text(0, 22, 335, y + 60, 110, "resets"); pdf_text(1, 26, 335, y + 88, 60, p); }
        p = bar ? bar + 1 : NULL;
    }
}

/* a one-page PDF: the JPEG template full-page plus the weather block; returns 1 on success */
static int compose_page(const char *jpg, const char *out) {
    FILE *f = fopen(jpg, "rb"); if (!f) return 0;
    fseek(f, 0, SEEK_END); long jl = ftell(f); fseek(f, 0, SEEK_SET);
    unsigned char *jd = malloc(jl); if (fread(jd, 1, jl, f) != (size_t)jl) { fclose(f); free(jd); return 0; }
    fclose(f);
    pdfn = 0;
    pdfn += snprintf(pdfbuf, sizeof pdfbuf, "q %.2f 0 0 %.2f 0 0 cm /Im1 Do Q\n", 1404 * PT, 1872 * PT);
    weather_block();
    usage_block();
    FILE *o = fopen(out, "wb"); if (!o) { free(jd); return 0; }
    long off[8]; int n = 0;
    n += fprintf(o, "%%PDF-1.4\n");
    off[1] = n; n += fprintf(o, "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n");
    off[2] = n; n += fprintf(o, "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n");
    off[3] = n; n += fprintf(o, "3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] /Contents 4 0 R /Resources << /XObject << /Im1 5 0 R >> /Font << /FB 6 0 R /FR 7 0 R >> >> >> endobj\n", 1404 * PT, 1872 * PT);
    off[4] = n; n += fprintf(o, "4 0 obj << /Length %zu >> stream\n", pdfn); n += fwrite(pdfbuf, 1, pdfn, o); n += fprintf(o, "\nendstream endobj\n");
    off[5] = n; n += fprintf(o, "5 0 obj << /Type /XObject /Subtype /Image /Width 1404 /Height 1872 /ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /DCTDecode /Length %ld >> stream\n", jl);
    n += fwrite(jd, 1, jl, o); n += fprintf(o, "\nendstream endobj\n");
    off[6] = n; n += fprintf(o, "6 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >> endobj\n");
    off[7] = n; n += fprintf(o, "7 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >> endobj\n");
    long xref = n;
    fprintf(o, "xref\n0 8\n0000000000 65535 f \n");
    for (int i = 1; i < 8; i++) fprintf(o, "%010ld 00000 n \n", off[i]);
    fprintf(o, "trailer << /Size 8 /Root 1 0 R >>\nstartxref\n%ld\n%%%%EOF\n", xref);
    fclose(o); free(jd);
    return 1;
}

/* The printed part of the page (date, calendar) is a stock of PDFs the installer left, one per day:
 * on a new day, while nothing is open on the tablet, today's page replaces the document's PDF and
 * xochitl is restarted so it shows it. The pen strokes on the page are kept: they belong to the .rm. */
static void swap_daily_page(void) {
    char today[16], printed[64] = "", src[700], tmp[700];
    time_t now = time(NULL); struct tm lt; localtime_r(&now, &lt);
    strftime(today, sizeof today, "%Y-%m-%d", &lt);
    char ppath[600]; snprintf(ppath, sizeof ppath, "%s/printed", dir);
    FILE *f = fopen(ppath, "r"); if (f) { if (fgets(printed, sizeof printed, f)) printed[strcspn(printed, "\r\n")] = 0; fclose(f); }
    long printed_at = 0; char pday[16] = "", presets[64] = ""; sscanf(printed, "%15s %ld %63s", pday, &printed_at, presets);
    char resets[64]; reset_texts(resets, sizeof resets);
    int new_day = strcmp(pday, today) != 0, stale = wx.ok && now - printed_at > 6 * 3600 && wx.at > printed_at;
    int resets_changed = resets[0] && strcmp(resets, presets) != 0;
    if (!pdf[0] || (!new_day && !stale && !resets_changed)) return;
    if (!home_screen()) return;                       /* only between documents: the restart reloads the tablet's app */
    snprintf(src, sizeof src, "%s/pages/%s.jpg", dir, today);
    if (access(src, R_OK)) { static int said = 0; if (!said++) fprintf(stderr, "no printed page for %s in the stock\n", today); return; }
    snprintf(tmp, sizeof tmp, "%s.new", pdf);
    if (!compose_page(src, tmp) || rename(tmp, pdf)) { fprintf(stderr, "could not compose the page for %s\n", today); return; }
    f = fopen(ppath, "w"); if (f) { fprintf(f, "%s %ld %s\n", today, (long)now, resets); fclose(f); }
    fprintf(stderr, "printed page for %s composed (%s), restarting xochitl\n", today, new_day ? "new day" : stale ? "fresh weather" : "new reset times");
    if (!getenv("RM_FIXTURES")) system("systemctl restart xochitl");
}

static void save_state(char shown[NZ][512], long rm_mtime) {
    char path[600]; snprintf(path, sizeof path, "%s/state", dir);
    FILE *f = fopen(path, "w"); if (!f) return;
    fprintf(f, "rm=%ld\n", rm_mtime);
    for (int z = 0; z < NZ; z++) fprintf(f, "zone_%s=%s\n", ZONES[z], shown[z]);
    fclose(f);
}

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: rmdash <dir> [xochitl.conf] [event device]\n"); return 1; }
    strncpy(dir, argv[1], sizeof dir - 1);
    parse_dev_args(argc, argv);
    if (!read_kv("config", "doc", doc, sizeof doc)) { fprintf(stderr, "config: no doc=\n"); return 1; }
    read_kv("config", "rm", rmfile, sizeof rmfile);
    read_kv("config", "pdf", pdf, sizeof pdf);
    read_kv("config", "city", city, sizeof city);
    if (read_kv("config", "lat", tmpv, sizeof tmpv)) lat = atof(tmpv);
    if (read_kv("config", "lon", tmpv, sizeof tmpv)) lon = atof(tmpv);
    if (read_kv("config", "minutes", tmpv, sizeof tmpv)) minutes = atol(tmpv);
    if (minutes < 1) minutes = 1;
    read_layout();
    for (int z = 0; z < NZ; z++) { char p[600]; snprintf(p, sizeof p, "%s/sweep_%s.bin", dir, ZONES[z]); zone_on[z] = !access(p, R_OK); }
    use_pc_timezone();
    setvbuf(stderr, NULL, _IOLBF, 0);
    journal_start(doc);
    static char shown[NZ][512], want[NZ][512];
    char state_path[600]; snprintf(state_path, sizeof state_path, "%s/state", dir);
    int active = 0, lost = 0;
    time_t fetched = 0;
    struct ink ink = {0};
    while (1) {
        if (page_lost) {                                    /* stopped mid-draw: forget what is drawn, erase everything next time */
            page_lost = 0; lost = 1;
            for (int z = 0; z < NZ; z++) shown[z][0] = 0;
            unlink(state_path);
        }
        int is_open = page_on_screen();
        if (!is_open) lost = 0;
        if (!is_open || lost) {
            if (active) {
                fprintf(stderr, "dashboard closed, stopping\n");
                close_device(); active = 0;
                if (shown[0][0] || shown[7][0]) {           /* xochitl saves the page on close; remember that version */
                    sleep(3);
                    save_state(shown, mtime(rmfile));
                }
            }
            if (time(NULL) - fetched >= minutes * 60) { fetch_weather(); fetch_usage(); fetched = time(NULL); }   /* keeps the printed weather fresh */
            if (!is_open) give_back_the_pen(doc);
            swap_daily_page();
            sleep(2);
            continue;
        }
        if (!active) {
            fprintf(stderr, "dashboard open, starting\n");
            if (!open_device()) { sleep(5); continue; }
            char saved[32]; int same = read_kv("state", "rm", saved, sizeof saved) && atol(saved) == mtime(rmfile) && atol(saved) != 0;
            for (int z = 0; z < NZ; z++) { char k[24]; snprintf(k, sizeof k, "zone_%s", ZONES[z]); if (!same || !read_kv("state", k, shown[z], 512)) shown[z][0] = 0; }
            if (!same && mtime(rmfile) == 0) fprintf(stderr, "fresh page, nothing to erase\n");   /* a pushed page has no strokes yet */
            else if (!same) {                               /* the page changed since we last drew: clean every zone */
                fprintf(stderr, "page changed since last time, erasing all zones\n");
                char name[32];
                for (int z = 0; z < NZ; z++) { snprintf(name, sizeof name, "sweep_%s.bin", ZONES[z]); stroke_file(name, 1, 0, 0, -1); }
                hover(START_SETTLE_US);
            } else fprintf(stderr, "page unchanged since last time, keeping what is drawn\n");
            active = 1;
            ink_reset(&ink, rmfile);
            if (page_lost) continue;
        }
        time_t now = time(NULL); struct tm lt; localtime_r(&now, &lt);
        want_all(want, &lt);
        int changed[NZ], any = 0;
        for (int z = 0; z < NZ; z++) { changed[z] = strcmp(want[z], shown[z]) != 0; any |= changed[z]; }
        for (int z = 0; z < NZ; z++) if (changed[z] && shown[z][0]) { draw_zone(z, shown[z], &lt, 1); if (page_lost) break; }   /* out with the old, along its own strokes */
        if (page_lost) continue;
        if (any) hover(START_SETTLE_US);
        for (int z = 0; z < NZ; z++) if (changed[z]) {
            long long t0 = now_us(); long f0 = frames_written;
            draw_zone(z, want[z], &lt, 0);
            if (page_lost) break;
            strcpy(shown[z], want[z]);
            fprintf(stderr, "%s: %s (%.0f s, %ld frames, %.1f ms/frame)\n", ZONES[z], want[z], (now_us() - t0) / 1e6, frames_written - f0,
                    frames_written > f0 ? (now_us() - t0) / 1e3 / (frames_written - f0) : 0.0);
        }
        if (page_lost) continue;
        if (any) ink_drawn(&ink);
        if (ink_lost(&ink) || ink_none(&ink) || ink_wiped(&ink)) { for (int z = 0; z < NZ; z++) shown[z][0] = 0; unlink(state_path); }
        if (time(NULL) - fetched >= minutes * 60) {        /* network only after drawing; what changed is drawn next round */
            long long t0 = now_us();
            if (zone_on[7]) fetch_weather();
            fetch_usage();
            fetched = time(NULL);
            fprintf(stderr, "data fetched in %.1f s\n", (now_us() - t0) / 1e6);
            continue;
        }
        long wait = 60 - (long)(time(NULL) % 60);
        while (wait > 0 && page_on_screen()) { nap(2000000); wait -= 2; }
    }
}
