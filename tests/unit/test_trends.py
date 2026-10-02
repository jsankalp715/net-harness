from __future__ import annotations

import json
import re

import pytest

from netharness.trends import MetricSeries, RunInfo, build_series, nice_ticks, render_html


def run(n: int, day: int, branch: str = "main") -> RunInfo:
    return RunInfo(1000 + n, n, f"{n:07d}abc", branch, "push", f"2026-10-{day:02d}T12:00:00Z")


KEY = "test_failover.py::test_link_failure_reconverges[ospf-r1-to-r2-cut-r1r2]::failover"


def test_build_series_orders_by_time_not_input_order() -> None:
    s = build_series([(run(3, 3), {KEY: 0.3}), (run(1, 1), {KEY: 0.1}), (run(2, 2), {"x::y": 9})])
    assert list(s) == sorted(s)
    assert s[KEY].values == [0.1, 0.3]  # run 2 has no KEY -> no gap point invented
    assert s["x::y"].values == [9]


def test_titles_split_case_and_label() -> None:
    s = MetricSeries(KEY)
    assert s.title == "ospf-r1-to-r2-cut-r1r2 · failover"
    assert s.subtitle == "test_failover.py::test_link_failure_reconverges"
    assert MetricSeries("test_ospf.py::test_initial_convergence::initial").title == (
        "test_initial_convergence · initial"
    )


@pytest.mark.parametrize(
    ("top", "expected"),
    [(0.39, [0, 0.2, 0.4]), (1.0, [0, 0.5, 1.0]), (7.3, [0, 2.5, 5, 7.5]), (0, [0, 1])],
)
def test_nice_ticks(top: float, expected: list[float]) -> None:
    assert nice_ticks(top) == expected


def test_render_html_has_one_chart_per_metric_and_table() -> None:
    series = build_series([(run(1, 1), {KEY: 0.1, "a::b": 1.0}), (run(2, 2), {KEY: 0.12})])
    page = render_html(series, {KEY: 0.11}, "2026-10-02 12:00 UTC")
    assert page.count("<svg") == 2
    assert page.count('class="ref"') == 1  # only KEY has a baseline
    assert page.count('class="hit"') == 3  # one hover target per point
    assert "<td>+0.01 s</td>" in page  # latest - baseline
    assert "<title>Convergence Trends</title>" in page


def test_tooltip_data_is_escaped() -> None:
    evil = run(1, 1, branch="<img src=x onerror=alert(1)>")
    page = render_html(build_series([(evil, {KEY: 0.1})]), {}, "now")
    assert "<img" not in page
    tip = re.search(r"data-tip='([^']*)'", page)
    assert tip is not None
    import html

    assert json.loads(html.unescape(tip.group(1)))["ref"].startswith("<img")
