"""Tests for Wilson CI and CSV I/O."""
import os
import tempfile

from iap_downstream.metrics import (
    DownstreamRow,
    read_downstream_csv,
    wilson_ci,
    write_downstream_csv,
)


def test_wilson_edges():
    assert wilson_ci(0, 0) == (0.0, 0.0)
    lo, hi = wilson_ci(5, 5)
    assert 0.0 <= lo <= 1.0 and 0.5 < hi <= 1.0
    lo0, hi0 = wilson_ci(0, 5)
    assert lo0 == 0.0 and 0.0 < hi0 < 0.6


def test_wilson_contains_point_estimate():
    for k, n in [(1, 4), (2, 5), (3, 10), (9, 9)]:
        lo, hi = wilson_ci(k, n)
        assert lo <= k / n <= hi


def test_csv_roundtrip():
    rows = [
        DownstreamRow("after", "C1", "craftBoat", "drift", 5, 5, 1.0, 0.57, 1.0, 4.0, 0.0),
        DownstreamRow("before", "C1", "craftBoat", "drift", 0, 5, 0.0, 0.0, 0.43, 2.0, 3.0),
    ]
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.csv")
        write_downstream_csv(rows, path)
        back = read_downstream_csv(path)
        assert len(back) == 2
        assert back[0]["method"] == "after"
        assert float(back[0]["success_rate"]) == 1.0
        assert back[1]["method"] == "before"
