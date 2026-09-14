"""联合求解器：跨全部帧整体优化（显式禁止逐帧贪心）。

用整数规划（CBC，随 PuLP 分发的开源求解器）对全部帧联合求解，
按三级字典序依次最小化：
  1. 最大单步距离（所有成员、所有相邻帧）；
  2. 所有相邻帧步距总和；
  3. 按（帧, 帧内点位输入顺序）连接的成员编号序列（Unicode 码点序）。

距离一律为二维欧氏距离。浮点目标分层时使用 EPS_OBJ 容差。

模型分两阶段构造：
  - 第一阶段用点变量 + 大 M 约束求最优最大单步 D*；
  - 第二、三阶段只保留不超过 D* 的边，改用稀疏的边变量模型，
    使逐位置的字典序求解保持轻快。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import pulp

from .parser import Problem, eligibility

EPS_FEAS = 1e-9  # 可行性容差（步长上限比较）
EPS_OBJ = 1e-6  # 目标分层容差（浮点）


@dataclass(frozen=True)
class StepMove:
    member: str
    from_point: int
    to_point: int
    distance: float


@dataclass(frozen=True)
class TransitionMoves:
    from_frame: int
    to_frame: int
    moves: tuple[StepMove, ...]
    max_step: float
    total_distance: float


@dataclass(frozen=True)
class Solution:
    assignment: tuple[tuple[str, ...], ...]  # assignment[t][p] = 成员编号
    transitions: tuple[TransitionMoves, ...]
    max_step: float
    total_distance: float
    sequence: tuple[str, ...]  # 按（帧, 点位输入顺序）连接的成员编号序列


def _cbc() -> pulp.LpSolver:
    solver = pulp.PULP_CBC_CMD(msg=False)
    if solver.available():
        return solver
    return pulp.PULP_CBC_CMD(path="cbc", msg=False)


class _BaseModel:
    """公共数据：资格、相邻帧距离。"""

    def __init__(self, problem: Problem, n_frames: int):
        self.problem = problem
        self.n_frames = n_frames
        self.n_members = len(problem.members)
        self.eligible = eligibility(problem)[:n_frames]
        self.eligible_set = [[set(row) for row in frame] for frame in self.eligible]
        frames = problem.frames[:n_frames]
        self.dist: list[list[list[float]]] = []
        for t in range(n_frames - 1):
            a, b = frames[t].points, frames[t + 1].points
            self.dist.append(
                [[math.hypot(pa.x - pb.x, pa.y - pb.y) for pb in b] for pa in a]
            )

    def edge_ok(self, t: int, p: int, q: int, m: int, step_cap: float | None) -> bool:
        d = self.dist[t][p][q]
        if d > self.problem.members[m].max_step + EPS_FEAS:
            return False
        if step_cap is not None and d > step_cap + EPS_FEAS:
            return False
        return True

    def common(self, t: int, p: int, q: int) -> list[int]:
        return sorted(self.eligible_set[t][p] & self.eligible_set[t + 1][q])


class _PointModel(_BaseModel):
    """点变量模型：用于第一阶段（最小化最大单步）与可行性判定。"""

    def __init__(self, problem: Problem, n_frames: int):
        super().__init__(problem, n_frames)
        self.prob = pulp.LpProblem("formation_points", pulp.LpMinimize)
        self.x: dict[tuple[int, int, int], pulp.LpVariable] = {}
        for t in range(n_frames):
            for p in range(self.n_members):
                for m in self.eligible[t][p]:
                    self.x[t, p, m] = pulp.LpVariable(f"x_{t}_{p}_{m}", cat="Binary")
        for t in range(n_frames):
            for p in range(self.n_members):
                self.prob += (
                    pulp.lpSum(self.x[t, p, m] for m in self.eligible[t][p]) == 1,
                    f"cover_point_{t}_{p}",
                )
            for m in range(self.n_members):
                spots = [p for p in range(self.n_members) if m in self.eligible_set[t][p]]
                self.prob += (
                    pulp.lpSum(self.x[t, p, m] for p in spots) == 1,
                    f"cover_member_{t}_{m}",
                )
        # 步长禁止边：超过成员上限的 (p, q) 不能同时被该成员占用
        for t in range(n_frames - 1):
            for p in range(self.n_members):
                for q in range(self.n_members):
                    for m in self.common(t, p, q):
                        if not self.edge_ok(t, p, q, m, None):
                            self.prob += self.x[t, p, m] + self.x[t + 1, q, m] <= 1

    def solve(self) -> bool:
        return self.prob.solve(_cbc()) == pulp.LpStatusOptimal


class _EdgeModel(_BaseModel):
    """边变量模型：只保留不超过 step_cap 的边，用于第二、三阶段。"""

    def __init__(self, problem: Problem, n_frames: int, step_cap: float):
        super().__init__(problem, n_frames)
        self.prob = pulp.LpProblem("formation_edges", pulp.LpMinimize)
        # x 由边变量流守恒自动取整，故用连续变量减少分支
        self.x: dict[tuple[int, int, int], pulp.LpVariable] = {}
        for t in range(n_frames):
            for p in range(self.n_members):
                for m in self.eligible[t][p]:
                    self.x[t, p, m] = pulp.LpVariable(f"x_{t}_{p}_{m}", lowBound=0, upBound=1)
        self.z: dict[tuple[int, int, int, int], pulp.LpVariable] = {}
        for t in range(n_frames - 1):
            for p in range(self.n_members):
                for q in range(self.n_members):
                    for m in self.common(t, p, q):
                        if self.edge_ok(t, p, q, m, step_cap):
                            self.z[t, p, q, m] = pulp.LpVariable(f"z_{t}_{p}_{q}_{m}", cat="Binary")
        for t in range(n_frames):
            for p in range(self.n_members):
                self.prob += (
                    pulp.lpSum(self.x[t, p, m] for m in self.eligible[t][p]) == 1,
                    f"cover_point_{t}_{p}",
                )
            for m in range(self.n_members):
                spots = [p for p in range(self.n_members) if m in self.eligible_set[t][p]]
                self.prob += (
                    pulp.lpSum(self.x[t, p, m] for p in spots) == 1,
                    f"cover_member_{t}_{m}",
                )
        # 流守恒：成员在某点的占用当且仅当有一条进入/离开的边
        for t in range(n_frames - 1):
            for p in range(self.n_members):
                for m in self.eligible[t][p]:
                    out = [self.z[t, p, q, m] for q in range(self.n_members) if (t, p, q, m) in self.z]
                    self.prob += self.x[t, p, m] == pulp.lpSum(out)
        for t in range(1, n_frames):
            for q in range(self.n_members):
                for m in self.eligible[t][q]:
                    inc = [self.z[t - 1, p, q, m] for p in range(self.n_members) if (t - 1, p, q, m) in self.z]
                    self.prob += self.x[t, q, m] == pulp.lpSum(inc)
        self.total_expr = pulp.lpSum(
            self.dist[t][p][q] * z for (t, p, q, m), z in self.z.items()
        )

    def solve(self) -> bool:
        return self.prob.solve(_cbc()) == pulp.LpStatusOptimal


def feasible(problem: Problem, n_frames: int) -> bool:
    """帧 0..n_frames-1 的前缀是否可行（供不可行见证定位首个失败前缀）。"""
    model = _PointModel(problem, n_frames)
    model.prob.setObjective(pulp.LpAffineExpression())  # 零目标，仅判可行
    return model.solve()


def _minimize_max_step(model: _PointModel) -> float | None:
    """第一阶段：最小化最大单步。返回最优值，不可行返回 None。"""
    big_d = pulp.LpVariable("max_step_var", lowBound=0)
    model.prob.setObjective(big_d)
    for t in range(model.n_frames - 1):
        for p in range(model.n_members):
            for q in range(model.n_members):
                d = model.dist[t][p][q]
                for m in model.common(t, p, q):
                    if model.edge_ok(t, p, q, m, None):
                        model.prob += big_d >= d * (model.x[t, p, m] + model.x[t + 1, q, m] - 1)
    if not model.solve():
        return None
    value = pulp.value(big_d)
    return 0.0 if value is None else float(value)


def _lexicographic_sequence(model: _EdgeModel) -> list[list[int]]:
    """第三阶段：逐位置字典序最小化成员编号序列（Unicode 码点序）。"""
    ids = [m.id for m in model.problem.members]
    order = sorted(range(model.n_members), key=lambda m: ids[m])
    rank = {m: r for r, m in enumerate(order)}
    assignment = [[-1] * model.n_members for _ in range(model.n_frames)]
    for t in range(model.n_frames):
        for p in range(model.n_members):
            elig = model.eligible[t][p]
            if len(elig) == 1:
                chosen = elig[0]  # 锁定或唯一候选，无需再解
            else:
                model.prob.setObjective(pulp.lpSum(rank[m] * model.x[t, p, m] for m in elig))
                if not model.solve():
                    raise RuntimeError("字典序阶段意外不可行")
                chosen = max(elig, key=lambda m: pulp.value(model.x[t, p, m]) or 0.0)
            model.prob += (model.x[t, p, chosen] == 1, f"fix_{t}_{p}")
            assignment[t][p] = chosen
    return assignment


def _build_solution(problem: Problem, assignment: list[list[int]]) -> Solution:
    n_frames = len(problem.frames)
    n_members = len(problem.members)
    ids = [m.id for m in problem.members]
    positions: list[list[int]] = []
    for t in range(n_frames):
        pos = [0] * n_members
        for p, m in enumerate(assignment[t]):
            pos[m] = p
        positions.append(pos)

    transitions: list[TransitionMoves] = []
    for t in range(n_frames - 1):
        moves: list[StepMove] = []
        for m in range(n_members):
            pa = problem.frames[t].points[positions[t][m]]
            pb = problem.frames[t + 1].points[positions[t + 1][m]]
            moves.append(
                StepMove(
                    member=ids[m],
                    from_point=positions[t][m],
                    to_point=positions[t + 1][m],
                    distance=math.hypot(pa.x - pb.x, pa.y - pb.y),
                )
            )
        moves.sort(key=lambda mv: mv.member)
        transitions.append(
            TransitionMoves(
                from_frame=t,
                to_frame=t + 1,
                moves=tuple(moves),
                max_step=max(mv.distance for mv in moves),
                total_distance=sum(mv.distance for mv in moves),
            )
        )

    return Solution(
        assignment=tuple(tuple(ids[m] for m in row) for row in assignment),
        transitions=tuple(transitions),
        max_step=max(tr.max_step for tr in transitions),
        total_distance=sum(tr.total_distance for tr in transitions),
        sequence=tuple(ids[m] for row in assignment for m in row),
    )


def solve(problem: Problem) -> Solution | None:
    """跨全部帧联合求解。无可行解返回 None。"""
    n_frames = len(problem.frames)

    # 第一阶段：最小化最大单步
    stage1 = _PointModel(problem, n_frames)
    d_star = _minimize_max_step(stage1)
    if d_star is None:
        return None

    # 第二阶段：在最大单步不超过 d_star 的稀疏边模型上，最小化步距总和
    stage2 = _EdgeModel(problem, n_frames, step_cap=d_star + EPS_OBJ * max(1.0, d_star))
    stage2.prob.setObjective(stage2.total_expr)
    if not stage2.solve():  # 第一阶段已保证可行，这里只是防御
        raise RuntimeError("第二阶段模型意外不可行")
    s_value = pulp.value(stage2.total_expr)
    s_star = 0.0 if s_value is None else float(s_value)

    # 第三阶段：保持前两阶段最优，字典序最小化成员编号序列
    stage2.prob += (
        stage2.total_expr <= s_star + EPS_OBJ * max(1.0, abs(s_star)),
        "keep_total_optimal",
    )
    assignment = _lexicographic_sequence(stage2)
    return _build_solution(problem, assignment)
