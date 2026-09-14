"""契约诊断：把 application/x-ndjson 请求体解析成 Problem，或给出全部诊断。

行号从 1 开始：第 1 行是成员定义，第 2 行起每行一帧（帧下标 = 行号 - 2）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import ValidationError

from .contracts import Diagnostic, FrameLine, MembersLine

MIN_FRAMES = 2
MAX_FRAMES = 10


@dataclass(frozen=True)
class Member:
    id: str
    roles: frozenset[str]
    max_step: float


@dataclass(frozen=True)
class Point:
    x: float
    y: float
    lock: str | None
    role: str | None


@dataclass(frozen=True)
class Frame:
    points: tuple[Point, ...]
    line: int  # 该帧在 NDJSON 中的行号（1 起）


@dataclass(frozen=True)
class Problem:
    members: tuple[Member, ...]
    frames: tuple[Frame, ...]

    @property
    def member_index(self) -> dict[str, int]:
        return {m.id: i for i, m in enumerate(self.members)}


def eligibility(problem: Problem) -> list[list[list[int]]]:
    """eligible[t][p] = 帧 t 第 p 点按锁定/角色可站的成员下标列表。"""
    out: list[list[list[int]]] = []
    for frame in problem.frames:
        row: list[list[int]] = []
        for point in frame.points:
            ok = [
                m
                for m, mem in enumerate(problem.members)
                if (point.lock is None or point.lock == mem.id)
                and (point.role is None or point.role in mem.roles)
            ]
            row.append(ok)
        out.append(row)
    return out


def _reject_constant(value: str) -> None:
    raise ValueError(f"不允许的数值常量 {value}（NaN/Infinity）")


def _loc_to_field(loc: tuple[object, ...]) -> str:
    parts: list[str] = []
    for item in loc:
        if isinstance(item, int):
            if parts:
                parts[-1] = f"{parts[-1]}[{item}]"
            else:
                parts.append(f"[{item}]")
        else:
            parts.append(str(item))
    return ".".join(parts)


def _validate(model: type[MembersLine] | type[FrameLine], data: object, line: int, diags: list[Diagnostic]):
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        for err in exc.errors():
            field = _loc_to_field(err.get("loc", ()))
            diags.append(
                Diagnostic(
                    line=line,
                    field=field or None,
                    code="INVALID_FIELD",
                    message=f"第 {line} 行字段 {field or '(根)'} 无效：{err.get('msg')}",
                )
            )
        return None


def parse_ndjson(text: str) -> tuple[Problem | None, list[Diagnostic]]:
    """解析整个请求体。任一错误都会使整单失败，返回全部诊断。"""
    raw_lines = text.split("\n")
    while raw_lines and raw_lines[-1].strip() == "":
        raw_lines.pop()  # 容忍末尾换行/空行
    if not raw_lines:
        return None, [
            Diagnostic(line=None, field=None, code="EMPTY_BODY", message="请求体为空：第 1 行必须是成员定义")
        ]

    diags: list[Diagnostic] = []
    parsed: list[object | None] = []
    for i, raw in enumerate(raw_lines, start=1):
        line = raw.rstrip("\r")
        if line.strip() == "":
            diags.append(
                Diagnostic(line=i, field=None, code="EMPTY_LINE", message=f"第 {i} 行是空行，每行必须是一个 JSON 对象")
            )
            parsed.append(None)
            continue
        try:
            parsed.append(json.loads(line, parse_constant=_reject_constant))
        except ValueError as exc:
            diags.append(
                Diagnostic(line=i, field=None, code="INVALID_JSON", message=f"第 {i} 行不是合法 JSON：{exc}")
            )
            parsed.append(None)
    if diags:
        return None, diags

    members_line = _validate(MembersLine, parsed[0], 1, diags)
    frame_lines = [_validate(FrameLine, data, i, diags) for i, data in enumerate(parsed[1:], start=2)]
    if diags:
        return None, diags
    assert members_line is not None and all(fl is not None for fl in frame_lines)

    # ---- 跨字段 / 跨行校验 ----
    if len(frame_lines) < MIN_FRAMES:
        diags.append(
            Diagnostic(
                line=None,
                field="frames",
                code="TOO_FEW_FRAMES",
                message=f"至少需要 {MIN_FRAMES} 帧（第 2 行起），实际 {len(frame_lines)} 帧",
            )
        )
    if len(frame_lines) > MAX_FRAMES:
        diags.append(
            Diagnostic(
                line=MAX_FRAMES + 2,
                field="frames",
                code="TOO_MANY_FRAMES",
                message=f"最多 {MAX_FRAMES} 帧，第 {MAX_FRAMES + 2} 行已超出",
            )
        )

    seen_ids: dict[str, int] = {}
    for idx, member in enumerate(members_line.members):
        if member.id in seen_ids:
            diags.append(
                Diagnostic(
                    line=1,
                    field=f"members[{idx}].id",
                    code="DUPLICATE_MEMBER",
                    message=f"第 1 行成员编号 {member.id!r} 与 members[{seen_ids[member.id]}] 重复",
                )
            )
        else:
            seen_ids[member.id] = idx

    n_members = len(members_line.members)
    for f_idx, frame in enumerate(frame_lines):
        line = f_idx + 2
        for p_idx, point in enumerate(frame.points):
            if point.lock is not None and point.lock not in seen_ids:
                diags.append(
                    Diagnostic(
                        line=line,
                        field=f"points[{p_idx}].lock",
                        code="UNKNOWN_LOCK",
                        message=f"第 {line} 行锁定了未定义的成员 {point.lock!r}",
                    )
                )
        if len(frame.points) != n_members:
            diags.append(
                Diagnostic(
                    line=line,
                    field="points",
                    code="POINT_COUNT_MISMATCH",
                    message=f"第 {line} 行有 {len(frame.points)} 个点，与成员数 {n_members} 不一致",
                )
            )
        seen_xy: dict[tuple[float, float], int] = {}
        for p_idx, point in enumerate(frame.points):
            key = (point.x, point.y)
            if key in seen_xy:
                diags.append(
                    Diagnostic(
                        line=line,
                        field=f"points[{p_idx}]",
                        code="DUPLICATE_COORDINATES",
                        message=f"第 {line} 行坐标 {key} 与 points[{seen_xy[key]}] 重复",
                    )
                )
            else:
                seen_xy[key] = p_idx

    # 首帧：每点必须锁定一名不同成员
    if frame_lines:
        first = frame_lines[0]
        first_locks: dict[str, int] = {}
        for p_idx, point in enumerate(first.points):
            if point.lock is None:
                diags.append(
                    Diagnostic(
                        line=2,
                        field=f"points[{p_idx}].lock",
                        code="FIRST_FRAME_LOCK_REQUIRED",
                        message=f"首帧（第 2 行）每个点都必须 lock 一名成员，points[{p_idx}] 缺少 lock",
                    )
                )
            elif point.lock in first_locks:
                diags.append(
                    Diagnostic(
                        line=2,
                        field=f"points[{p_idx}].lock",
                        code="FIRST_FRAME_LOCK_NOT_UNIQUE",
                        message=f"首帧成员 {point.lock!r} 被锁定到多个点（points[{first_locks[point.lock]}] 与 points[{p_idx}]）",
                    )
                )
            else:
                first_locks[point.lock] = p_idx

    if diags:
        return None, diags

    members = tuple(
        Member(id=m.id, roles=frozenset(m.roles), max_step=m.max_step) for m in members_line.members
    )
    frames = tuple(
        Frame(
            points=tuple(Point(x=p.x, y=p.y, lock=p.lock, role=p.role) for p in frame.points),
            line=f_idx + 2,
        )
        for f_idx, frame in enumerate(frame_lines)
    )
    return Problem(members=members, frames=frames), []
