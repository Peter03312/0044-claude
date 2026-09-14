"""数值精度回归测试：

- 方案非常接近时，高优先级目标（最大单步、总路程）不得被低优先级目标牺牲；
- 坐标很大（仍合法有限）或很小时，求解与见证都必须保持正确。
"""
from __future__ import annotations

import math

from app.solver import solve
from app.witness import build_witness
from tests.test_solver import make_problem


def test_max_step_priority_over_total_with_tiny_gap():
    """方案 X 最大单步 5.0、总路程 9.0；方案 Y 最大单步 5.000002、总路程 6.000002。

    字典序下必须选 X（最大单步优先），即使 Y 的总路程小得多。
    两者最大单步仅差 2e-6，用于回归"分层容差过松导致目标越级"的缺陷。
    """
    # 构造 B：|B->p1=(1,0)| = 4，|B->p0=(5,0)| = 5.000002（圆交点）
    r1, r0, d = 4.0, 5.000002, 4.0
    a = (r1 * r1 - r0 * r0 + d * d) / (2 * d)
    h = math.sqrt(r1 * r1 - a * a)
    bx, by = 1.0 + a, h
    assert math.isclose(math.hypot(bx - 1, by), 4.0, rel_tol=1e-12)
    assert math.isclose(math.hypot(bx - 5, by), 5.000002, rel_tol=1e-12)

    problem = make_problem(
        members=[("A", [], 10.0), ("B", [], 10.0)],
        frames=[
            [(0, 0, "A", None), (bx, by, "B", None)],
            [(5, 0, None, None), (1, 0, None, None)],
        ],
    )
    sol = solve(problem)
    assert sol is not None
    assert sol.assignment[1] == ("A", "B")  # A->(5,0), B->(1,0)：最大单步 5.0 的方案
    assert math.isclose(sol.max_step, 5.0, rel_tol=1e-12)
    assert math.isclose(sol.total_distance, 9.0, rel_tol=1e-12)


def test_total_priority_over_sequence_with_tiny_gap():
    """两方案最大单步同为 50，总路程相差 2e-6；总路程小者编号序列反而更大。

    字典序下必须选总路程小者（总路程优先于编号序列）。
    """
    # p1 使 |a->p1| 比 |b->p1| 小 2e-6（相对 (0,0) 与 (0,6)）
    delta = 2e-6 / 1.2  # 该处距离差对 y 的导数约为 1.2
    p1y = 3.0 - delta
    problem = make_problem(
        members=[("a", [], 10.0), ("b", [], 10.0), ("c", [], 100.0)],
        frames=[
            [(0, 0, "a", None), (0, 6, "b", None), (100, 0, "c", None)],
            [(4, 3, None, None), (4, p1y, None, None), (100, 50, None, None)],
        ],
    )
    sol = solve(problem)
    assert sol is not None
    assert math.isclose(sol.max_step, 50.0, rel_tol=1e-12)  # c 的走位主导，两方案相同
    # 总路程小者：b->(4,3), a->(4,p1y)，即帧1序列 (b, a, c)
    assert sol.assignment[1] == ("b", "a", "c")
    assert math.isclose(sol.total_distance, 5 + math.hypot(4, p1y) + 50.0, rel_tol=1e-9)
    assert sol.sequence == ("a", "b", "c", "b", "a", "c")


def test_huge_coordinates_feasible():
    """坐标 ~1e150（合法有限）时，可行的走位必须正常求出。"""
    big = 1e150
    problem = make_problem(
        members=[("A", [], 2 * big), ("B", [], 2 * big)],
        frames=[
            [(big, 0, "A", None), (-big, 0, "B", None)],
            [(big, big, None, None), (-big, big, None, None)],
        ],
    )
    sol = solve(problem)
    assert sol is not None
    assert sol.assignment[1] == ("A", "B")
    assert sol.max_step == big
    assert sol.total_distance == 2 * big


def test_tiny_coordinates_feasible():
    """坐标 ~1e-12 时同样正确（尺度归一后与普通坐标无异）。"""
    problem = make_problem(
        members=[("A", [], 2e-12), ("B", [], 2e-12)],
        frames=[
            [(0, 0, "A", None), (2e-12, 0, "B", None)],
            [(1e-12, 0, None, None), (3e-12, 0, None, None)],
        ],
    )
    sol = solve(problem)
    assert sol is not None
    assert sol.assignment[1] == ("A", "B")
    assert math.isclose(sol.max_step, 1e-12, rel_tol=1e-9)


def test_witness_step_tolerance_is_scale_aware():
    """微小坐标下的步距违反也必须被见证正确标记（容差随尺度缩放）。"""
    problem = make_problem(
        members=[("a", [], 1e-13), ("b", [], 1e-13)],
        frames=[
            [(0, 0, "a", None), (0, 1e-12, "b", None)],
            [(5e-13, 0, None, None), (0, 1e-12, None, None)],
        ],
    )
    report = build_witness(problem)
    assert report.first_infeasible_prefix_end.frame_index == 1
    far = report.points[0]
    assert far.candidates == []
    assert {v.code for v in far.violations} == {"STEP_TOO_FAR"}
    assert {v.member for v in far.violations} == {"a", "b"}
    assert report.uncovered_points == [0]
