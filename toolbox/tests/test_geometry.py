import numpy as np
import pytest

from lwp.geometry import box_from_points, capsule, distance, swept_box


def test_parallel_pipes_exact_clearance():
    a = capsule([0, 0, 0], [1000, 0, 0], 50)
    b = capsule([0, 200, 0], [1000, 200, 0], 30)
    d, _ = distance(a, b)
    assert d == pytest.approx(120)


def test_crossing_pipes_overlap():
    a = capsule([0, 0, 0], [1000, 0, 0], 50)
    b = capsule([500, -500, 20], [500, 500, 20], 50)
    d, where = distance(a, b)
    assert d == pytest.approx(-80)
    assert where[0] == pytest.approx(500)


def test_tray_above_pipe_face_to_point():
    # 200 wide x 100 high tray centred at z=300 (bottom 250); pipe radius 50 at z=0 (top 50) → clear 200
    tray = swept_box([0, 0, 300], [2000, 0, 300], [0, 1, 0], 100, 50)
    pipe = capsule([1000, -1000, 0], [1000, 1000, 0], 50)
    d, _ = distance(tray, pipe)
    assert d == pytest.approx(200, abs=0.5)


def test_diagonal_boxes_use_true_distance():
    # Boxes offset 100 in Y and 100 in Z: true gap is sqrt(2)*100, not the SAT lower bound 100.
    a = swept_box([0, 0, 0], [1000, 0, 0], [0, 1, 0], 50, 50)
    b = swept_box([0, 200, 200], [1000, 200, 200], [0, 1, 0], 50, 50)
    d, _ = distance(a, b)
    assert d == pytest.approx(100 * np.sqrt(2), abs=0.5)


def test_box_through_beam_is_overlap():
    beam = box_from_points(np.array([[x, y, z] for x in (0, 6000) for y in (-150, 150) for z in (4600, 5350)]))
    pipe = capsule([3000, -2000, 4950], [3000, 2000, 4950], 60)
    d, _ = distance(beam, pipe)
    assert d < 0


def test_rect_section_axis_orientation():
    duct = swept_box([0, 0, 0], [1000, 0, 0], [0, 1, 0], 800, 160)   # 1600 x 320
    lo, hi = duct.aabb()
    assert hi[1] - lo[1] == pytest.approx(1600)
    assert hi[2] - lo[2] == pytest.approx(320)
