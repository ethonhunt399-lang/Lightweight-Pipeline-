import numpy as np

from lwp.headroom_map import HeadroomMap
from lwp.lanes import _changes, lane_headroom


def test_changes_merges_short_runs():
    lv = np.array([2600.0] * 10 + [2400.0] * 2 + [2600.0] * 10 + [2800.0] * 5)
    assert _changes(lv, 1000.0, 3000.0) == 1


def test_lane_profile_takes_lowest_across_and_skips_stalls():
    # 20 m × 10 m map, 0.5 m cells, 2.6 m clear; a duct at 2.4 m crossing at x = 10 m.
    nx, ny = 40, 20
    clear = np.full((ny, nx), 2600.0)
    clear[:, 20] = 2400.0
    clear[:4, 30] = 2000.0                        # low point over a stall (south side), not over the lane
    hmap = HeadroomMap(0.0, 0.0, 500.0, nx, ny, 0.0, clear, np.zeros((ny, nx), dtype=np.int32), ["k"])
    plan = {"lanes": [[[0.0, 5000.0], [20000.0, 5000.0]]],
            "stalls": [{"pts": [[14000.0, 0.0], [17000.0, 0.0], [17000.0, 2000.0], [14000.0, 2000.0]]}]}
    r = lane_headroom(hmap, plan)
    lane = r["lanes"][0]
    assert lane["min_mm"] == 2400 and lane["median_mm"] == 2600
    assert lane["changes"] == 0                   # a 1 m dip is merged
