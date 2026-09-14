"""请求/响应契约：NDJSON 行模型、诊断结构与响应模型。

所有输入模型使用 strict 模式并禁止多余字段，保证坏行能被精确诊断。
"""
from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 成员编号 / 角色名：非空字符串，长度设上限防止滥用
Name = Annotated[str, Field(min_length=1, max_length=64)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


def _require_finite(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("数值必须是有限值（不允许 NaN 或 Infinity）")
    return value


class MemberIn(_StrictModel):
    """首行中一名成员的定义。"""

    id: Name
    roles: list[Name] = Field(default_factory=list)
    max_step: Annotated[float, Field(ge=0)]

    @field_validator("max_step")
    @classmethod
    def _finite(cls, value: float) -> float:
        return _require_finite(value)


class MembersLine(_StrictModel):
    """NDJSON 第 1 行：成员名单。"""

    members: list[MemberIn] = Field(min_length=1)


class PointIn(_StrictModel):
    """一帧中的一个点位。"""

    x: float
    y: float
    lock: Name | None = None
    role: Name | None = None

    @field_validator("x", "y")
    @classmethod
    def _finite(cls, value: float) -> float:
        return _require_finite(value)


class FrameLine(_StrictModel):
    """NDJSON 第 2 行起：一帧的有序点位列表。"""

    points: list[PointIn] = Field(min_length=1)


class Diagnostic(BaseModel):
    """单条输入诊断：行号（1 起）、字段路径、错误码与说明。"""

    line: int | None
    field: str | None
    code: str
    message: str


# ---------- 成功响应 ----------


class AssignmentOut(BaseModel):
    point_index: int
    member: str


class FrameOut(BaseModel):
    frame_index: int
    line: int
    assignments: list[AssignmentOut]


class StepOut(BaseModel):
    member: str
    from_point: int
    to_point: int
    distance: float


class TransitionOut(BaseModel):
    from_frame: int
    to_frame: int
    from_line: int
    to_line: int
    steps: list[StepOut]
    max_step: float
    total_distance: float


class SortingBasis(BaseModel):
    """排序依据：三级字典序目标的取值。"""

    objective_order: list[str]
    max_step: float
    total_distance: float
    member_sequence: list[str]
    description: str


class OkResponse(BaseModel):
    status: Literal["ok"] = "ok"
    frames: list[FrameOut]
    transitions: list[TransitionOut]
    sorting_basis: SortingBasis


# ---------- 不可行见证 ----------


class PointViolation(BaseModel):
    """某成员相对某点位违反的一条约束。"""

    member: str
    code: Literal["LOCKED_TO_OTHER", "MISSING_ROLE", "STEP_TOO_FAR"]
    detail: str
    locked_to: str | None = None
    required_role: str | None = None
    needed_distance: float | None = None
    max_step: float | None = None
    nearest_point: int | None = None


class PointWitness(BaseModel):
    point_index: int
    x: float
    y: float
    lock: str | None
    role: str | None
    candidates: list[str]
    violations: list[PointViolation]


class PrefixEnd(BaseModel):
    frame_index: int
    line: int


class HallDeficiency(BaseModel):
    """资格二部图上的霍尔亏缺：这些成员的可选点位不足以分配。"""

    members: list[str]
    points: list[int]


class InfeasibleResponse(BaseModel):
    status: Literal["infeasible"] = "infeasible"
    first_infeasible_prefix_end: PrefixEnd
    feasible_frames: int
    points: list[PointWitness]
    uncovered_points: list[int]
    unplaceable_members: list[str]
    hall_deficiency: HallDeficiency | None
    note: str
