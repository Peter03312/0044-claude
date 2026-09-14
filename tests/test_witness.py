"""不可行见证测试：首个不可行前缀定位与逐点诊断。"""
from __future__ import annotations

from app.witness import build_witness
from tests.test_solver import make_problem


def test_witness_step_too_far():
    problem = make_problem(
        members=[("a", [], 1.0), ("b", [], 1.0)],
        frames=[
            [(0, 0, "a", None), (0, 1, "b", None)],
            [(5, 0, None, None), (0, 1, None, None)],
        ],
    )
    report = build_witness(problem)
    assert report.first_infeasible_prefix_end.frame_index == 1
    assert report.first_infeasible_prefix_end.line == 3
    assert report.feasible_frames == 1
    far = report.points[0]
    assert far.candidates == []
    assert {v.member for v in far.violations} == {"a", "b"}
    assert all(v.code == "STEP_TOO_FAR" for v in far.violations)
    va = next(v for v in far.violations if v.member == "a")
    assert va.needed_distance == 5.0 and va.max_step == 1.0
    assert report.uncovered_points == [0]


def test_witness_missing_role():
    problem = make_problem(
        members=[("a", [], 10.0), ("b", [], 10.0)],
        frames=[
            [(0, 0, "a", None), (1, 0, "b", None)],
            [(0, 1, None, None), (1, 1, None, "lead")],
        ],
    )
    report = build_witness(problem)
    assert report.first_infeasible_prefix_end.frame_index == 1
    role_point = report.points[1]
    assert role_point.candidates == []
    assert {v.code for v in role_point.violations} == {"MISSING_ROLE"}
    assert {v.member for v in role_point.violations} == {"a", "b"}
    assert report.uncovered_points == [1]


def test_witness_double_lock_hall_deficiency():
    problem = make_problem(
        members=[("a", [], 10.0), ("b", [], 10.0)],
        frames=[
            [(0, 0, "a", None), (1, 0, "b", None)],
            [(0, 1, "a", None), (1, 1, "a", None)],
        ],
    )
    report = build_witness(problem)
    assert report.first_infeasible_prefix_end.frame_index == 1
    assert report.unplaceable_members == ["b"]
    assert report.hall_deficiency is not None
    assert report.hall_deficiency.members == ["b"]
    assert report.hall_deficiency.points == []
    for point in report.points:
        vb = next(v for v in point.violations if v.member == "b")
        assert vb.code == "LOCKED_TO_OTHER"
        assert vb.locked_to == "a"


def test_witness_first_frame_infeasible():
    """首帧即不可行：锁定成员不具备该点要求的角色。"""
    problem = make_problem(
        members=[("a", [], 10.0), ("b", ["lead"], 10.0)],
        frames=[
            [(0, 0, "a", "lead"), (1, 0, "b", None)],
            [(0, 1, None, None), (1, 1, None, None)],
        ],
    )
    report = build_witness(problem)
    assert report.first_infeasible_prefix_end.frame_index == 0
    assert report.first_infeasible_prefix_end.line == 2
    point0 = report.points[0]
    assert point0.candidates == []
    va = next(v for v in point0.violations if v.member == "a")
    assert va.code == "MISSING_ROLE"
    vb = next(v for v in point0.violations if v.member == "b")
    assert vb.code == "LOCKED_TO_OTHER"
