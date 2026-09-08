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
    assert len(xs) == 101 and xs == sorted(xs)                              # 400px / STEP_PX + start point
    assert max(b - a for a, b in zip(xs, xs[1:])) <= rm_ai.VirtualStylus.STEP_PX * 15725 / 1404 + 1
    assert evs.count((rm_ai.EV_SYN, rm_ai.SYN_REPORT, 0)) == len(xs) + 9   # one frame per point + 9 for hover/touch/hold/lift
    assert evs.index((rm_ai.EV_KEY, rm_ai.BTN_TOUCH, 1)) > evs.index((rm_ai.EV_ABS, rm_ai.ABS_DISTANCE, 10))
    assert evs[-2] == (rm_ai.EV_KEY, rm_ai.BTN_TOOL_PEN, 0)


def test_erasing_a_segment_covers_it_and_never_touches_its_neighbours():
    ERASER_R = rm_ai.SevenSegmentDigit.ERASER_R

    def footprint(path, r):  # bounding box of the path swept by a disc of radius r
        xs = [p[0] for p in path]; ys = [p[1] for p in path]
        return min(xs) - r, min(ys) - r, max(xs) + r, max(ys) + r

    def disjoint(a, b):
        return a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]

    built = 0
    for w, h in [(50, 90), (75, 140), (95, 175), (120, 220)]:
        for pen_width in (6, 12, 18, 24, 32):
            try:
                d = rm_ai.SevenSegmentDigit(None, 100, 100, w, h, pen_width=pen_width)
            except ValueError:
                continue
            built += 1
            pen_r = pen_width / 2
            for seg, erase in d.erase_coords.items():
                e = footprint(erase, ERASER_R)
                own = footprint(d.draw_coords[seg], pen_r)
                assert e[0] <= own[0] and e[1] <= own[1] and e[2] >= own[2] and e[3] >= own[3], (w, h, pen_width, seg)
                # the eraser passes must overlap each other, or a strip of ink survives between them
                across = sorted({p[1] if seg in "ADG" else p[0] for p in erase})
                assert all(b - a <= 2 * ERASER_R for a, b in zip(across, across[1:])), (w, h, pen_width, seg)
                for other, draw in d.draw_coords.items():
                    if other != seg:
                        assert disjoint(e, footprint(draw, pen_r)), (w, h, pen_width, seg, other)
    assert built >= 12
    d = rm_ai.SevenSegmentDigit(None, 0, 0, 95, 175, pen_width=12)   # the geometry verified on a tablet
    assert d.gap == 18 and len(d.erase_coords["A"]) == 4
    try:
        rm_ai.SevenSegmentDigit(None, 0, 0, 75, 140, pen_width=40)
        assert False, "a 40px pen cannot fit a medium digit"
    except ValueError:
        pass


if __name__ == "__main__":
    test_stroke_is_resampled_hovered_and_lifted()
    test_erasing_a_segment_covers_it_and_never_touches_its_neighbours()
    print("ok")
