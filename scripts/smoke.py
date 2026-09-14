#!/usr/bin/env python3
"""HTTP 冒烟测试：对运行中的 API 逐项检查，并打印可直接抄写的连续走位。

用法：API_BASE_URL=http://localhost:8000 python scripts/smoke.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("API_BASE_URL", "http://localhost:8000")

MEMBERS = {"members": [
    {"id": "阿雅", "roles": ["lead"], "max_step": 2.0},
    {"id": "小贝", "roles": [], "max_step": 2.0},
    {"id": "小澄", "roles": [], "max_step": 2.0},
    {"id": "阿朵", "roles": [], "max_step": 2.0},
]}
FRAME0 = {"points": [
    {"x": 0, "y": 0, "lock": "阿雅"}, {"x": 2, "y": 0, "lock": "小贝"},
    {"x": 0, "y": 2, "lock": "小澄"}, {"x": 2, "y": 2, "lock": "阿朵"},
]}
FRAME1 = {"points": [
    {"x": 1, "y": 0, "role": "lead"}, {"x": 3, "y": 0}, {"x": 0, "y": 3}, {"x": 2, "y": 3},
]}
FRAME2 = {"points": [
    {"x": 1, "y": 1, "lock": "阿雅"}, {"x": 3, "y": 1}, {"x": 0, "y": 4}, {"x": 2, "y": 4},
]}
FRAMES = [FRAME0, FRAME1, FRAME2]

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'通过' if ok else '失败'}] {name}" + (f"：{detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def request(method: str, path: str, body: str | None = None, content_type: str = "application/x-ndjson"):
    req = urllib.request.Request(
        BASE + path,
        data=body.encode("utf-8") if body is not None else None,
        headers={"Content-Type": content_type},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def print_walk(payload: dict, frames: list[dict]) -> None:
    print("\n===== 连续走位（可直接抄写）=====")
    for frame in payload["frames"]:
        points = frames[frame["frame_index"]]["points"]
        print(f"第 {frame['frame_index'] + 1} 帧（NDJSON 第 {frame['line']} 行）:")
        for a in frame["assignments"]:
            pt = points[a["point_index"]]
            print(f"  点{a['point_index']} ({pt['x']}, {pt['y']}) ← {a['member']}")
    for tr in payload["transitions"]:
        print(f"走位 第{tr['from_frame'] + 1}帧 → 第{tr['to_frame'] + 1}帧:")
        for step in tr["steps"]:
            print(f"  {step['member']}: 点{step['from_point']} → 点{step['to_point']}，步距 {step['distance']:.4f}")
    basis = payload["sorting_basis"]
    print(f"排序依据: 最大单步 {basis['max_step']:.4f} → 总步距 {basis['total_distance']:.4f} "
          f"→ 编号序列 {basis['member_sequence']}")
    print("================================\n")


def main() -> int:
    print(f"冒烟目标: {BASE}")

    status, body = request("GET", "/health")
    check("GET /health 返回 200", status == 200 and body.get("status") == "ok", str(body))

    ndjson_ok = "\n".join(json.dumps(x, ensure_ascii=False) for x in [MEMBERS, *FRAMES]) + "\n"
    status, body = request("POST", "/solve", ndjson_ok)
    check("POST /solve 正常单返回 200 且 status=ok", status == 200 and body.get("status") == "ok", str(body)[:300])
    if status == 200 and body.get("status") == "ok":
        check("最大单步为 1.0", abs(body["sorting_basis"]["max_step"] - 1.0) < 1e-9)
        print_walk(body, FRAMES)

    members_slow = {"members": [dict(m, max_step=0.5) for m in MEMBERS["members"]]}
    ndjson_bad_walk = "\n".join(json.dumps(x, ensure_ascii=False) for x in [members_slow, *FRAMES]) + "\n"
    status, body = request("POST", "/solve", ndjson_bad_walk)
    check(
        "无解单返回 200 且 status=infeasible、定位到帧1",
        status == 200 and body.get("status") == "infeasible"
        and body["first_infeasible_prefix_end"]["frame_index"] == 1,
        str(body)[:300],
    )

    ndjson_bad_line = ndjson_ok + "{broken json\n"
    status, body = request("POST", "/solve", ndjson_bad_line)
    errors = body.get("detail", {}).get("errors", []) if isinstance(body, dict) else []
    check(
        "坏行返回 422 且带行号",
        status == 422 and any(e.get("line") == 5 and e.get("code") == "INVALID_JSON" for e in errors),
        str(body)[:300],
    )

    status, _ = request("POST", "/solve", ndjson_ok, content_type="application/json")
    check("错误媒体类型返回 415", status == 415, str(status))

    if FAILURES:
        print(f"\n冒烟失败 {len(FAILURES)} 项: {FAILURES}")
        return 1
    print("\n冒烟全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
