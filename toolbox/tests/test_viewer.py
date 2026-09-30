import pytest

from lwp.viewer import _clip_segment


def test_clip_segment_inside_and_crossing():
    rect = (0, 0, 10, 10)
    assert _clip_segment((1, 1), (2, 2), rect) == ((1, 1), (2, 2))
    (a, b) = _clip_segment((-10, 5), (20, 5), rect)
    assert a == pytest.approx((0, 5)) and b == pytest.approx((10, 5))


def test_clip_segment_outside():
    assert _clip_segment((-5, -5), (-1, 20), (0, 0, 10, 10)) is None
