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
    st.proc, st.tool, st.FRAME_DT = FakeProc(), None, 0
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


if __name__ == "__main__":
    test_stroke_is_resampled_hovered_and_lifted()
    test_bars_reach_the_wanted_thickness_and_erasing_never_touches_neighbours()
    test_claude_usage_summary_from_the_documented_report_shapes()
    test_weather_summary_from_the_open_meteo_shape()
    print("ok")
