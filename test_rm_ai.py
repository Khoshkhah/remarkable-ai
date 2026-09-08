"""Smallest checks that fail if stylus pacing or the 7-segment geometry regresses.  Run: python3 test_rm_ai.py"""
import io
import struct

import rm_ai


class FakeProc:
    def __init__(self):
        self.stdin = io.BytesIO()


def events(buf):
    return [struct.unpack("<IIHHi", buf[i:i + 16])[2:] for i in range(0, len(buf), 16)]


def test_stroke_is_resampled_hovered_and_lifted():
    st = rm_ai.VirtualStylus.__new__(rm_ai.VirtualStylus)
    st.proc, st.tool, st.FRAME_DT, st.real_until, st.sent, st.guard, st.recheck = FakeProc(), None, 0, 0.0, {}, None, False
    st.stroke([(100, 100), (500, 100)], is_eraser=False, pressure=2500)
    evs = events(st.proc.stdin.getvalue())
    xs = [v for t, c, v in evs if t == rm_ai.EV_ABS and c == rm_ai.ABS_Y]  # ABS_Y is the horizontal axis
    import math
    expected = math.ceil(400 / rm_ai.VirtualStylus.STEP_PX) + 1   # resampled to STEP_PX plus the start point
    assert len(xs) == expected and xs == sorted(xs)
    assert max(b - a for a, b in zip(xs, xs[1:])) <= rm_ai.VirtualStylus.STEP_PX * 15725 / 1404 + 1
    assert evs.count((rm_ai.EV_SYN, rm_ai.SYN_REPORT, 0)) == len(xs) + 9   # one frame per point + 9 for hover/touch/hold/lift
    assert evs.index((rm_ai.EV_KEY, rm_ai.BTN_TOUCH, 1)) > evs.index((rm_ai.EV_ABS, rm_ai.ABS_DISTANCE, 10))
    assert evs[-2] == (rm_ai.EV_KEY, rm_ai.BTN_TOOL_PEN, 0)


def test_bars_reach_the_wanted_thickness_and_erasing_never_touches_neighbours():
    ERASER_R = rm_ai.SevenSegmentDigit.ERASER_R

    def footprint(path, r):  # bounding box of the path swept by a disc of radius r
        xs = [p[0] for p in path]; ys = [p[1] for p in path]
        return min(xs) - r, min(ys) - r, max(xs) + r, max(ys) + r

    def disjoint(a, b):
        return a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]

    def across(path, seg):  # perpendicular offsets of the passes of a bar
        return sorted({p[1] if seg in "ADG" else p[0] for p in path})

    built = 0
    for w, h in [(50, 90), (75, 140), (95, 175), (120, 220)]:
        for thickness, pen in [(12, 12), (18, 12), (24, 12), (24, 18), (32, 13), (12, 8)]:
            try:
                d = rm_ai.SevenSegmentDigit(None, 100, 100, w, h, thickness=thickness, pen=pen)
            except ValueError:
                continue
            built += 1
            want = max(thickness, pen)
            for seg, erase in d.erase_coords.items():
                draw = d.draw_coords[seg]
                ink = footprint(draw, pen / 2)
                # the passes together are exactly as thick as asked, and overlap each other
                assert abs((ink[3] - ink[1] if seg in "ADG" else ink[2] - ink[0]) - want) < 1e-6, (w, h, thickness, pen, seg)
                a = across(draw, seg)
                assert all(b_ - a_ <= pen / 2 + 1e-6 for a_, b_ in zip(a, a[1:])), (thickness, pen, seg)
                # the eraser covers all of it, its passes overlap, and it never reaches another bar's ink
                e = footprint(erase, ERASER_R)
                assert e[0] <= ink[0] and e[1] <= ink[1] and e[2] >= ink[2] and e[3] >= ink[3], (w, h, thickness, pen, seg)
                a = across(erase, seg)
                assert all(b_ - a_ <= 2 * ERASER_R for a_, b_ in zip(a, a[1:])), (thickness, pen, seg)
                for other, odraw in d.draw_coords.items():
                    if other != seg:
                        assert disjoint(e, footprint(odraw, pen / 2)), (w, h, thickness, pen, seg, other)
    assert built >= 14
    d = rm_ai.SevenSegmentDigit(None, 0, 0, 95, 175, thickness=12, pen=12)   # the geometry verified on a tablet
    assert d.gap == 18 and d.draw_offsets == [0] and len(d.erase_coords["A"]) == 4
    try:
        rm_ai.SevenSegmentDigit(None, 0, 0, 75, 140, thickness=40, pen=12)
        assert False, "40px bars cannot fit a medium digit"
    except ValueError:
        pass


def test_claude_usage_summary_from_the_documented_report_shapes():
    import datetime
    cost = {"data": [
        {"starting_at": "2026-09-01T00:00:00Z", "results": [{"amount": "123.78912", "currency": "USD"}, {"amount": "76.21088", "currency": "USD"}]},
        {"starting_at": "2026-09-07T00:00:00Z", "results": [{"amount": "50", "currency": "USD"}]},
        {"starting_at": "2026-08-31T00:00:00Z", "results": [{"amount": "999", "currency": "USD"}]},   # last month: excluded
        {"starting_at": "2026-09-05T00:00:00Z", "results": []},                                        # empty day
    ]}
    usage = {"data": [{"starting_at": "2026-09-07T00:00:00Z", "results": [
        {"cache_creation": {"ephemeral_1h_input_tokens": 1000, "ephemeral_5m_input_tokens": 500},
         "cache_read_input_tokens": 200, "output_tokens": 500, "uncached_input_tokens": 1500}]}]}
    u = rm_ai.summarize_claude_usage(cost, usage, datetime.date(2026, 9, 7))
    assert abs(u["month_usd"] - 2.50) < 1e-9 and abs(u["today_usd"] - 0.50) < 1e-9
    assert u["tokens_in"] == 3200 and u["tokens_out"] == 500
    assert rm_ai.fetch_claude_usage() is None or "ANTHROPIC_ADMIN_KEY" in __import__("os").environ


def test_weather_summary_from_the_open_meteo_shape():
    data = {"current": {"temperature_2m": 13.6, "apparent_temperature": 11.2, "weather_code": 2, "wind_speed_10m": 4.4},
            "daily": {"time": ["2026-09-08", "2026-09-09"], "weather_code": [2, 61],
                      "temperature_2m_max": [18.1, 15.0], "temperature_2m_min": [9.4, 8.0],
                      "precipitation_probability_max": [10, 70],
                      "sunrise": ["2026-09-08T06:12", "2026-09-09T06:14"], "sunset": ["2026-09-08T19:35", "2026-09-09T19:32"]}}
    w = rm_ai.summarize_weather(data, "Stockholm")
    assert w["name"] == "Stockholm" and w["text"] == "Partly cloudy" and w["wind"] == 4.4
    assert w["days"][1] == {"dow": "WED", "text": "Light rain", "max": 15.0, "min": 8.0, "pop": 70, "sunrise": "06:14", "sunset": "19:32"}


def test_box_detection_accepts_rectangles_and_rejects_lines_and_loops():
    import math
    def rect(x, y, w, h, wobble=0):
        pts = [(x + w * i / 20, y) for i in range(21)] + [(x + w, y + h * i / 20) for i in range(21)]
        pts += [(x + w - w * i / 20, y + h) for i in range(21)] + [(x, y + h - h * i / 20) for i in range(21)]
        return [(px + wobble * math.sin(i), py + wobble * math.cos(i)) for i, (px, py) in enumerate(pts)]
    assert rm_ai.is_box(rect(100, 100, 300, 120))
    assert rm_ai.is_box(rect(100, 100, 300, 120, wobble=6))            # hand-drawn wobble
    assert not rm_ai.is_box([(100 + 5 * i, 100) for i in range(60)])    # a line
    circle = [(300 + 150 * math.cos(t / 40 * 2 * math.pi), 200 + 100 * math.sin(t / 40 * 2 * math.pi)) for t in range(41)]
    assert rm_ai.is_box(circle)                                         # an oval around text counts too
    assert not rm_ai.is_box(rect(100, 100, 300, 120)[:60])              # an open three-sided shape
    assert not rm_ai.is_box(rect(100, 100, 300, 120) * 3)               # a triple scribble around it
    assert rm_ai.inside((300, 200), circle) and not rm_ai.inside((100, 100), circle)


def test_baked_clock_strokes_are_the_live_strokes_under_the_names_the_replayer_expects():
    import tempfile
    from pathlib import Path
    clock = rm_ai.DigitalClock(host="-", pos="center", size="large", thickness=28, pressure=4000, pen_width=12, frame=True, ink_width=70)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        rm_ai.bake_clock_app(clock, out)
        names = sorted(p.name for p in out.iterdir())
        assert names == sorted([f"{k}{i}{s}.bin" for k in "de" for i in range(6) for s in "ABCDEFG"] + [f"colon{i}.bin" for i in range(4)] + ["frame.bin"])
        st = rm_ai.VirtualStylus.__new__(rm_ai.VirtualStylus)   # the live stylus, sending the same segment
        st.proc, st.tool, st.FRAME_DT, st.real_until, st.sent, st.guard, st.recheck = FakeProc(), None, 0, 0.0, {}, None, False
        st.stroke(clock.digits[2].draw_coords["G"], pressure=4000)
        assert events((out / "d2G.bin").read_bytes()) == events(st.proc.stdin.getvalue())
        for name in names:   # every file is one whole stroke: tool in first, tool out last, frames in between
            evs = events((out / name).read_bytes())
            tool = rm_ai.BTN_TOOL_RUBBER if name[0] == "e" else rm_ai.BTN_TOOL_PEN
            assert evs[0] == (rm_ai.EV_KEY, tool, 1) and evs[-2] == (rm_ai.EV_KEY, tool, 0) and evs[-1][0] == rm_ai.EV_SYN


def test_baked_dashboard_has_every_glyph_zone_and_bar_the_tablet_program_expects():
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        rm_ai.bake_dash_app(out)
        names = {p.name for p in out.iterdir()}
        for size, chars in rm_ai.GLYPH_SETS.items():
            assert all(f"g{size}_{ord(c)}.bin" in names for c in chars), size
        assert all(f"sweep_{z}.bin" in names for z in rm_ai.TABLET_DASH_LAYOUT["zones"])
        assert {"bar0.bin", "bar1.bin", "bar2.bin", "ebar0.bin", "e190_48.bin", "e40_65.bin", "layout", "glyphs"} <= names
        # the eraser of a glyph runs along the same strokes, three passes each, as eraser strokes
        erase = events((out / "e190_49.bin").read_bytes())
        assert sum(1 for t, c, v in erase if t == rm_ai.EV_KEY and c == rm_ai.BTN_TOOL_RUBBER and v == 1) == len(rm_ai.ERASE_OFFSETS) * len(rm_ai.STROKE_FONT["1"][1])
        passes = rm_ai.erase_paths([(0, 0), (100, 0)])
        assert len(passes) == len(rm_ai.ERASE_OFFSETS) and passes[0][0][0] == -6 and {round(p[0][1], 1) for p in passes} == {-10, -3.5, 3.5, 10}
        glyphs = {(int(a), int(b)): float(c) for a, b, c in (l.split() for l in (out / "glyphs").read_text().splitlines())}
        assert glyphs[(40, ord(" "))] > 0 and glyphs[(190, ord("0"))] > glyphs[(110, ord("0"))] > glyphs[(40, ord("0"))]
        layout = (out / "layout").read_text()
        assert f"base {rm_ai.GLYPH_BASE[0]} {rm_ai.GLYPH_BASE[1]}" in layout and "text clock 190 80 115" in layout and "bar 0 278 521 1013" in layout
        # single-line glyphs: a digit is one to three strokes, its first pen-down near the glyph base
        evs = events((out / "g190_48.bin").read_bytes())
        assert 1 <= sum(1 for t, c, v in evs if t == rm_ai.EV_KEY and c == rm_ai.BTN_TOOL_PEN and v == 1) <= 3
        first_y = next(v for t, c, v in evs if t == rm_ai.EV_ABS and c == rm_ai.ABS_Y)
        assert abs(first_y * 1404 / 15725 - rm_ai.GLYPH_BASE[0]) < 120
        assert all(c in rm_ai.STROKE_FONT for chars in rm_ai.GLYPH_SETS.values() for c in chars)
        rm_ai.render_dash_pages(out, "Burnaby", None, days=2)
        assert len(list((out / "pages").glob("*.jpg"))) == 3
        rm_ai.render_sleep_backgrounds(out, "Burnaby", None, days=1)
        rle = next((out / "sleep").glob("*.rle")).read_bytes()
        assert len(rle) % 3 == 0 and sum(rle[i + 1] | (rle[i + 2] << 8) for i in range(0, len(rle), 3)) == 1404 * 1872
        rm_ai.bake_font_atlases(out)
        assert (out / "f96b.atlas").read_bytes()[:4] == b"ATLS" and len(rm_ai.SLEEP_CHARS) == 97
    assert rm_ai.rle_encode(b"\xff\xff\x00") == b"\xff\x02\x00\x00\x01\x00"
    assert rm_ai.usage_file_text({"windows": [("Session", 12.4, "2026-09-08T21:59:59+00:00")]}).count("\n") == 5


if __name__ == "__main__":
    test_stroke_is_resampled_hovered_and_lifted()
    test_bars_reach_the_wanted_thickness_and_erasing_never_touches_neighbours()
    test_claude_usage_summary_from_the_documented_report_shapes()
    test_weather_summary_from_the_open_meteo_shape()
    test_box_detection_accepts_rectangles_and_rejects_lines_and_loops()
    test_baked_clock_strokes_are_the_live_strokes_under_the_names_the_replayer_expects()
    test_baked_dashboard_has_every_glyph_zone_and_bar_the_tablet_program_expects()
    print("ok")
