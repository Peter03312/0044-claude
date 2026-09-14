"""不可行见证：定位首个不可行前缀的末帧，并给出逐点诊断。

整体不可行性由联合求解器确认；本模块负责解释：
  - 首个不可行前缀的末帧（帧下标与 NDJSON 行号）；
  - 该帧每个点位上，各成员违反的锁定 / 角色 / 步距条件；
  - 资格二部图上的霍尔亏缺（哪些成员的可选点位不够分）。

步距诊断为成对检查：成员 m 在末帧点 q 的步距违反，是相对于上一帧中
m 所有可按锁定/角色站立的点取最近距离判定的（忽略其他成员的占用），
因此它是必要条件层面的解释，整体判定仍以联合求解器为准。
"""
from __future__ import annotations

import math

from .contracts import (
    HallDeficiency,
    InfeasibleResponse,
    PointViolation,
    PointWitness,
    PrefixEnd,
)
from .parser import Problem, eligibility
from .solver import EPS_FEAS, feasible

NOTE = (
    "逐点诊断为锁定/角色/步距的成对检查：步距相对于上一帧中该成员所有可站立点取最近值，"
    "未考虑其他成员的占用；整体不可行性由跨帧联合求解器确认。"
)


def _hall_deficiency(adj: list[list[int]], n_points: int) -> tuple[list[int], list[int]] | None:
    """成员→点位资格二部图上的霍尔亏缺。

    adj[m] 为成员 m 可站的点位列表。匹配不满时返回 (成员集合, 邻域点位集合)，
    满足 |邻域| < |成员|；匹配完整返回 None。
    """
    match_point = [-1] * n_points  # point -> member

    def augment(m: int, seen: list[bool]) -> bool:
        for p in adj[m]:
            if seen[p]:
                continue
            seen[p] = True
            if match_point[p] == -1 or augment(match_point[p], seen):
                match_point[p] = m
                return True
        return False

    for m in range(len(adj)):
        augment(m, [False] * n_points)
    free_members = [m for m in range(len(adj)) if m not in set(match_point)]
    if not free_members:
        return None
    # 从空配成员出发沿交错路可达的点集（Kőnig 定理）
    z_members = set(free_members)
    z_points: set[int] = set()
    stack = list(free_members)
    while stack:
        m = stack.pop()
        for p in adj[m]:
            if p in z_points:
                continue
            z_points.add(p)
            owner = match_point[p]
            if owner != -1 and owner not in z_members:
                z_members.add(owner)
                stack.append(owner)
    return sorted(z_members), sorted(z_points)


def build_witness(problem: Problem) -> InfeasibleResponse:
    """对整体不可行的问题，定位首个不可行前缀并逐点诊断。"""
    n_frames = len(problem.frames)
    n_members = len(problem.members)
    bad = 0
    for k in range(n_frames):
        if not feasible(problem, k + 1):
            bad = k
            break
    frame = problem.frames[bad]
    prev = problem.frames[bad - 1] if bad > 0 else None
    elig = eligibility(problem)

    points_out: list[PointWitness] = []
    for q, point in enumerate(frame.points):
        candidates: list[str] = []
        violations: list[PointViolation] = []
        for m, mem in enumerate(problem.members):
            broken: list[PointViolation] = []
            if point.lock is not None and point.lock != mem.id:
                broken.append(
                    PointViolation(
                        member=mem.id,
                        code="LOCKED_TO_OTHER",
                        detail=f"该点锁定给 {point.lock}，{mem.id} 不能占用",
                        locked_to=point.lock,
                    )
                )
            if point.role is not None and point.role not in mem.roles:
                broken.append(
                    PointViolation(
                        member=mem.id,
                        code="MISSING_ROLE",
                        detail=f"该点要求角色 {point.role!r}，{mem.id} 不具备",
                        required_role=point.role,
                    )
                )
            if broken:
                violations.extend(broken)
                continue
            if prev is not None:
                best_p, best_d = -1, math.inf
                for p in range(n_members):
                    if m not in elig[bad - 1][p]:
                        continue
                    pa = prev.points[p]
                    d = math.hypot(pa.x - point.x, pa.y - point.y)
                    if d < best_d:
                        best_p, best_d = p, d
                if best_d > mem.max_step + EPS_FEAS:
                    violations.append(
                        PointViolation(
                            member=mem.id,
                            code="STEP_TOO_FAR",
                            detail=(
                                f"{mem.id} 从上一帧最近的可站立点 points[{best_p}] 到该点需 "
                                f"{best_d:.6g}，超过其单步上限 {mem.max_step:.6g}"
                            ),
                            needed_distance=best_d,
                            max_step=mem.max_step,
                            nearest_point=best_p,
                        )
                    )
                    continue
            candidates.append(mem.id)
        points_out.append(
            PointWitness(
                point_index=q,
                x=point.x,
                y=point.y,
                lock=point.lock,
                role=point.role,
                candidates=candidates,
                violations=violations,
            )
        )

    uncovered = [pw.point_index for pw in points_out if not pw.candidates]
    placeable = {name for pw in points_out for name in pw.candidates}
    unplaceable = [mem.id for mem in problem.members if mem.id not in placeable]

    hall = None
    adj = [[] for _ in range(n_members)]
    for p, row in enumerate(elig[bad]):
        for m in row:
            adj[m].append(p)
    deficiency = _hall_deficiency(adj, n_members)
    if deficiency is not None:
        member_ids, point_ids = deficiency
        hall = HallDeficiency(
            members=[problem.members[m].id for m in member_ids],
            points=point_ids,
        )

    return InfeasibleResponse(
        first_infeasible_prefix_end=PrefixEnd(frame_index=bad, line=bad + 2),
        feasible_frames=bad,
        points=points_out,
        uncovered_points=uncovered,
        unplaceable_members=unplaceable,
        hall_deficiency=hall,
        note=NOTE,
    )
