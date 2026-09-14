"""FastAPI 入口：POST /solve 接收 application/x-ndjson，GET /health 健康检查。"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from .contracts import (
    AssignmentOut,
    FrameOut,
    OkResponse,
    SortingBasis,
    StepOut,
    TransitionOut,
)
from .parser import Problem, parse_ndjson
from .solver import Solution, solve
from .witness import build_witness

NDJSON_MEDIA_TYPE = "application/x-ndjson"

app = FastAPI(
    title="舞蹈队形走位 API",
    version="1.0.0",
    summary="跨全部帧联合求解成员-点位分配（非逐帧贪心）",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _error(status_code: int, message: str, errors: list[dict] | None = None) -> JSONResponse:
    detail: dict = {"message": message}
    if errors is not None:
        detail["errors"] = errors
    return JSONResponse(status_code=status_code, content={"detail": detail})


def _ok_payload(problem: Problem, solution: Solution) -> OkResponse:
    frames = [
        FrameOut(
            frame_index=t,
            line=t + 2,
            assignments=[
                AssignmentOut(point_index=p, member=member)
                for p, member in enumerate(row)
            ],
        )
        for t, row in enumerate(solution.assignment)
    ]
    transitions = [
        TransitionOut(
            from_frame=tr.from_frame,
            to_frame=tr.to_frame,
            from_line=tr.from_frame + 2,
            to_line=tr.to_frame + 2,
            steps=[
                StepOut(
                    member=mv.member,
                    from_point=mv.from_point,
                    to_point=mv.to_point,
                    distance=mv.distance,
                )
                for mv in tr.moves
            ],
            max_step=tr.max_step,
            total_distance=tr.total_distance,
        )
        for tr in solution.transitions
    ]
    return OkResponse(
        frames=frames,
        transitions=transitions,
        sorting_basis=SortingBasis(
            objective_order=["max_step", "total_distance", "member_sequence"],
            max_step=solution.max_step,
            total_distance=solution.total_distance,
            member_sequence=list(solution.sequence),
            description=(
                "跨全部帧联合优化，按字典序依次最小化：最大单步距离 → 相邻帧步距总和 → "
                "按（帧, 帧内点位输入顺序）连接的成员编号序列（Unicode 码点比较）。"
            ),
        ),
    )


@app.post("/solve", response_model=OkResponse)
async def solve_endpoint(request: Request):
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != NDJSON_MEDIA_TYPE:
        return _error(
            415,
            f"仅支持 Content-Type: {NDJSON_MEDIA_TYPE}",
            [{"line": None, "field": None, "code": "UNSUPPORTED_MEDIA_TYPE",
              "message": f"收到 {content_type or '(未提供)'}，应为 {NDJSON_MEDIA_TYPE}"}],
        )
    raw = await request.body()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return _error(
            422,
            "请求体必须是 UTF-8 编码",
            [{"line": None, "field": None, "code": "INVALID_ENCODING",
              "message": "请求体不是合法 UTF-8"}],
        )
    problem, diagnostics = parse_ndjson(text)
    if diagnostics:
        return _error(422, "请求未通过校验，整单被拒绝", [d.model_dump() for d in diagnostics])
    assert problem is not None

    solution = await run_in_threadpool(solve, problem)
    if solution is None:
        report = await run_in_threadpool(build_witness, problem)
        return JSONResponse(status_code=200, content=report.model_dump())
    return _ok_payload(problem, solution)
