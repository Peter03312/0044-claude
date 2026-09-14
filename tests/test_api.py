"""API 测试：成功流程、不可行见证、坏行 422、媒体类型 415。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

MEMBERS = {"members": [
    {"id": "阿雅", "roles": ["lead"], "max_step": 2.0},
    {"id": "小贝", "roles": [], "max_step": 2.0},
    {"id": "小澄", "roles": [], "max_step": 2.0},
    {"id": "阿朵", "roles": [], "max_step": 2.0},
]}
FRAME0 = {"points": [
    {"x": 0, "y": 0, "lock": "阿雅"},
    {"x": 2, "y": 0, "lock": "小贝"},
    {"x": 0, "y": 2, "lock": "小澄"},
    {"x": 2, "y": 2, "lock": "阿朵"},
]}
FRAME1 = {"points": [
    {"x": 1, "y": 0, "role": "lead"},
    {"x": 3, "y": 0},
    {"x": 0, "y": 3},
    {"x": 2, "y": 3},
]}
FRAME2 = {"points": [
    {"x": 1, "y": 1, "lock": "阿雅"},
    {"x": 3, "y": 1},
    {"x": 0, "y": 4},
    {"x": 2, "y": 4},
]}

TWO_MEMBERS = {"members": [{"id": "a", "roles": [], "max_step": 5.0}, {"id": "b", "roles": [], "max_step": 5.0}]}
TWO_FRAME0 = {"points": [{"x": 0, "y": 0, "lock": "a"}, {"x": 1, "y": 0, "lock": "b"}]}
TWO_FRAME1 = {"points": [{"x": 0, "y": 1}, {"x": 1, "y": 1}]}


def ndjson(*lines) -> str:
    # str 原样透传（用于构造坏行），其余按 JSON 序列化
    return "\n".join(line if isinstance(line, str) else json.dumps(line, ensure_ascii=False) for line in lines) + "\n"


def post(body: str, content_type: str = "application/x-ndjson"):
    return client.post("/solve", content=body.encode("utf-8"), headers={"Content-Type": content_type})


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_solve_ok():
    resp = post(ndjson(MEMBERS, FRAME0, FRAME1, FRAME2))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert len(body["frames"]) == 3
    assert body["frames"][0]["line"] == 2
    first = body["frames"][0]["assignments"]
    assert first[0] == {"point_index": 0, "member": "阿雅"}
    assert body["frames"][1]["assignments"][0]["member"] == "阿雅"  # role=lead 匹配
    basis = body["sorting_basis"]
    assert basis["objective_order"] == ["max_step", "total_distance", "member_sequence"]
    assert basis["max_step"] == pytest.approx(1.0)
    assert basis["total_distance"] == pytest.approx(8.0)
    assert basis["member_sequence"] == ["阿雅", "小贝", "小澄", "阿朵"] * 3
    assert len(body["transitions"]) == 2
    for transition in body["transitions"]:
        assert len(transition["steps"]) == 4
        for step in transition["steps"]:
            assert step["distance"] == pytest.approx(1.0)


def test_solve_infeasible_returns_witness():
    members = {"members": [dict(m, max_step=0.5) for m in MEMBERS["members"]]}
    resp = post(ndjson(members, FRAME0, FRAME1, FRAME2))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "infeasible"
    assert body["first_infeasible_prefix_end"] == {"frame_index": 1, "line": 3}
    assert body["feasible_frames"] == 1
    assert len(body["points"]) == 4
    assert any(
        v["code"] == "STEP_TOO_FAR"
        for point in body["points"]
        for v in point["violations"]
    )


def test_unsupported_media_type():
    resp = post(ndjson(MEMBERS, FRAME0, FRAME1), content_type="application/json")
    assert resp.status_code == 415


def test_empty_body():
    resp = post("")
    assert resp.status_code == 422
    assert resp.json()["detail"]["errors"][0]["code"] == "EMPTY_BODY"


@pytest.mark.parametrize(
    "lines, line_no, field, code",
    [
        # 坏 JSON：第 3 行
        ([TWO_MEMBERS, TWO_FRAME0, "{not json"], 3, None, "INVALID_JSON"),
        # 中间空行
        ([TWO_MEMBERS, "", TWO_FRAME1], 2, None, "EMPTY_LINE"),
        # 重复成员
        ([{"members": [{"id": "a", "max_step": 1}, {"id": "a", "max_step": 1}]}, TWO_FRAME0, TWO_FRAME1],
         1, "members[1].id", "DUPLICATE_MEMBER"),
        # 同帧重复坐标
        ([TWO_MEMBERS, {"points": [{"x": 0, "y": 0, "lock": "a"}, {"x": 0, "y": 0, "lock": "b"}]}, TWO_FRAME1],
         2, "points[1]", "DUPLICATE_COORDINATES"),
        # 未知锁定
        ([TWO_MEMBERS, TWO_FRAME0, {"points": [{"x": 0, "y": 1, "lock": "nobody"}, {"x": 1, "y": 1}]}],
         3, "points[0].lock", "UNKNOWN_LOCK"),
        # 首帧缺 lock
        ([TWO_MEMBERS, {"points": [{"x": 0, "y": 0, "lock": "a"}, {"x": 1, "y": 0}]}, TWO_FRAME1],
         2, "points[1].lock", "FIRST_FRAME_LOCK_REQUIRED"),
        # 首帧同一成员被锁两次
        ([TWO_MEMBERS, {"points": [{"x": 0, "y": 0, "lock": "a"}, {"x": 1, "y": 0, "lock": "a"}]}, TWO_FRAME1],
         2, "points[1].lock", "FIRST_FRAME_LOCK_NOT_UNIQUE"),
        # 点数与成员数不一致
        ([TWO_MEMBERS, TWO_FRAME0, {"points": [{"x": 0, "y": 1}]}],
         3, "points", "POINT_COUNT_MISMATCH"),
        # 只有一帧
        ([TWO_MEMBERS, TWO_FRAME0], None, "frames", "TOO_FEW_FRAMES"),
        # 负的单步上限
        ([{"members": [{"id": "a", "max_step": -1}, {"id": "b", "max_step": 1}]}, TWO_FRAME0, TWO_FRAME1],
         1, "members[0].max_step", "INVALID_FIELD"),
        # 多余字段
        ([{"members": [{"id": "a", "max_step": 1, "nickname": "x"}, {"id": "b", "max_step": 1}]}, TWO_FRAME0, TWO_FRAME1],
         1, "members[0].nickname", "INVALID_FIELD"),
        # 坐标为字符串（strict 模式拒绝）
        ([TWO_MEMBERS, {"points": [{"x": "0", "y": 0, "lock": "a"}, {"x": 1, "y": 0, "lock": "b"}]}, TWO_FRAME1],
         2, "points[0].x", "INVALID_FIELD"),
        # 坐标为布尔（strict 模式拒绝）
        ([TWO_MEMBERS, {"points": [{"x": True, "y": 0, "lock": "a"}, {"x": 1, "y": 0, "lock": "b"}]}, TWO_FRAME1],
         2, "points[0].x", "INVALID_FIELD"),
        # 1e999 溢出为 Infinity（原始行文本）
        ([TWO_MEMBERS, '{"points": [{"x": 1e999, "y": 0, "lock": "a"}, {"x": 1, "y": 0, "lock": "b"}]}', TWO_FRAME1],
         2, "points[0].x", "INVALID_FIELD"),
    ],
)
def test_bad_request_returns_422(lines, line_no, field, code):
    resp = post(ndjson(*lines))
    assert resp.status_code == 422, resp.text
    errors = resp.json()["detail"]["errors"]
    assert any(e["line"] == line_no and e["field"] == field and e["code"] == code for e in errors), errors


def test_too_many_frames():
    lines = [TWO_MEMBERS] + [
        {"points": [{"x": 0, "y": t, "lock": "a"}, {"x": 1, "y": t, "lock": "b"}]}
        if t == 0
        else {"points": [{"x": 0, "y": t}, {"x": 1, "y": t}]}
        for t in range(11)
    ]
    resp = post(ndjson(*lines))
    assert resp.status_code == 422
    errors = resp.json()["detail"]["errors"]
    assert any(e["code"] == "TOO_MANY_FRAMES" and e["line"] == 12 for e in errors)


def test_nan_rejected():
    body = ndjson(TWO_MEMBERS, TWO_FRAME0) + '{"points": [{"x": NaN, "y": 1}, {"x": 1, "y": 1}]}\n'
    resp = post(body)
    assert resp.status_code == 422
    assert resp.json()["detail"]["errors"][0]["code"] == "INVALID_JSON"
    assert resp.json()["detail"]["errors"][0]["line"] == 3


def test_trailing_newline_and_crlf_tolerated():
    body = ndjson(TWO_MEMBERS, TWO_FRAME0, TWO_FRAME1).replace("\n", "\r\n")
    resp = post(body)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
