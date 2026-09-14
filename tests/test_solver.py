"""联合求解器测试：对称误配、未来锚点、并列方案、无解。"""
from __future__ import annotations

import math

from app.parser import Frame, Member, Point, Problem
from app.solver import solve


def make_problem(members, frames) -> Problem:
    return Problem(
        members=tuple(Member(id=i, roles=frozenset(r), max_step=s) for i, r, s in members),
        frames=tuple(
            Frame(
                points=tuple(Point(x=x, y=y, lock=lock, role=role) for x, y, lock, role in frame),
                line=t + 2,
            )
            for t, frame in enumerate(frames)
        ),
    )


def test_symmetric_formation_identity_not_swapped():
    """对称队形：逐帧最近点会交换身份，联合求解必须让领舞到达锁定位置。"""
    problem = make_problem(
        members=[("lead", [], 3.0), ("mate", [], 3.0)],
        frames=[
            [(0, 0, "lead", None), (4, 0, "mate", None)],
            [(2, -1, None, None), (2, 1, None, None)],  # 两个对称点，输入顺序在前的是陷阱
            [(4, 2, "lead", None), (4, -2, "mate", None)],
        ],
    )
    sol = solve(problem)
    assert sol is not None
    # lead 必须走 (2,1)（点1），否则第 3 帧锁定点 (4,2) 超出单步上限
    assert sol.assignment[1] == ("mate", "lead")
    assert sol.assignment[2] == ("lead", "mate")
    assert math.isclose(sol.max_step, math.sqrt(5), rel_tol=1e-6)
    assert math.isclose(sol.total_distance, 4 * math.sqrt(5), rel_tol=1e-6)


def test_future_anchor_pulls_earlier_frame():
    """未来锚点：后续帧的锁定迫使前一帧放弃逐帧最近点。"""
    problem = make_problem(
        members=[("a", [], 1.5), ("b", [], 1.5)],
        frames=[
            [(0, 0, "a", None), (0, 1, "b", None)],
            [(1, 0, None, None), (1, 1, None, None)],  # 逐帧最近点：a→(1,0), b→(1,1)
            [(1, -1, "b", None), (1, 2, "a", None)],  # 但锁定要求 a→(1,1), b→(1,0)
        ],
    )
    sol = solve(problem)
    assert sol is not None
    assert sol.assignment[1] == ("b", "a")
    assert sol.assignment[2][1] == "a"
    assert math.isclose(sol.max_step, math.sqrt(2), rel_tol=1e-6)


def test_tie_broken_by_unicode_code_point():
    """并列方案：前两档目标打平时，按成员编号序列的 Unicode 码点序取最小。"""
    problem = make_problem(
        members=[("ada", [], 5.0), ("Zoe", [], 5.0)],
        frames=[
            [(0, 0, "ada", None), (0, 2, "Zoe", None)],
            [(1, 1, None, None), (-1, 1, None, None)],  # 两种分配的最大步/总步完全相同
        ],
    )
    sol = solve(problem)
    assert sol is not None
    assert math.isclose(sol.max_step, math.sqrt(2), rel_tol=1e-6)
    # "Zoe" 的码点 ("Z"=U+005A) 小于 "ada" ("a"=U+0061)，故 Zoe 占据帧1的点0
    assert sol.assignment[1] == ("Zoe", "ada")
    assert sol.sequence == ("ada", "Zoe", "Zoe", "ada")


def test_infeasible_returns_none():
    """无解：某点要求无人具备的角色。"""
    problem = make_problem(
        members=[("a", [], 10.0), ("b", [], 10.0)],
        frames=[
            [(0, 0, "a", None), (1, 0, "b", None)],
            [(0, 1, None, None), (1, 1, None, "lead")],
        ],
    )
    assert solve(problem) is None


def test_larger_problem_is_deterministic():
    """较大输入：结果确定且每帧都是双射。"""
    members = [(f"m{i}", [], 1.0) for i in range(5)]
    frames = [
        [(i, t, f"m{i}", None) for i in range(5)] if t == 0 else [(i, t, None, None) for i in range(5)]
        for t in range(4)
    ]
    problem = make_problem(members, frames)
    first = solve(problem)
    second = solve(problem)
    assert first is not None and second is not None
    assert first == second
    assert math.isclose(first.max_step, 1.0, rel_tol=1e-9)
    assert math.isclose(first.total_distance, 15.0, rel_tol=1e-9)
    for row in first.assignment:
        assert sorted(row) == sorted(f"m{i}" for i in range(5))
