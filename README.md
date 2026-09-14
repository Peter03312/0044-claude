# 舞蹈队形走位 API

面向 12–16 岁舞蹈社团的纯后端服务：把共创队形画成点位卡（NDJSON 逐行提交），
服务端**跨全部帧联合求解**每名成员每一帧站在哪个点——不是逐帧贪心，
因此对称队形中不会交换身份，领舞一定能走到被锁定的位置。

## 快速开始（Docker Compose）

```bash
docker compose up -d api          # 常驻 API，默认宿主端口 8000
API_PORT=9000 docker compose up -d api   # 用 API_PORT 覆盖宿主端口
```

一次性验证（自动起 api、跑 pytest 和 HTTP 冒烟，并打印可直接抄写的连续走位）：

```bash
docker compose run --rm verify
```

本地开发（Python 3.12）：

```bash
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
pytest
```

## 请求格式

- 方法：`POST /solve`，Content-Type：`application/x-ndjson`（其他类型返回 415）。
- body 为 NDJSON，每行一个 JSON 对象：
  - **第 1 行**：成员定义；
  - **第 2 行起**：每行一帧的有序点位，**2 至 10 行**（即 2–10 帧）。

```ndjson
{"members": [{"id": "阿雅", "roles": ["lead"], "max_step": 2.0}, {"id": "小贝", "roles": [], "max_step": 2.0}]}
{"points": [{"x": 0, "y": 0, "lock": "阿雅"}, {"x": 2, "y": 0, "lock": "小贝"}]}
{"points": [{"x": 1, "y": 0, "role": "lead"}, {"x": 3, "y": 0}]}
{"points": [{"x": 1, "y": 1, "lock": "阿雅"}, {"x": 3, "y": 1}]}
```

curl 示例：

```bash
curl -X POST http://localhost:8000/solve \
  -H 'Content-Type: application/x-ndjson' \
  --data-binary @example.ndjson
```

### 字段说明与单位

| 字段 | 含义 |
| --- | --- |
| `members[].id` | 成员编号，非空字符串，全单唯一（可用中文名） |
| `members[].roles` | 该成员具备的角色集合，可空；重复项自动去重 |
| `members[].max_step` | 该成员相邻两帧之间允许的最大单步距离，≥ 0 |
| `points[].x / y` | 点位坐标，有限数值（拒绝 NaN/Infinity） |
| `points[].lock` | 可选；该点只能由指定成员占用 |
| `points[].role` | 可选；该点只能由具备此角色的成员占用（成员含该角色即匹配） |

**单位**：坐标是同一平面上的任意单位（格、米均可），全单保持一致即可；
距离一律为二维**欧氏距离**，`max_step` 与坐标同单位。时间与角度不参与计算。

规则：

- 每帧点数必须等于成员数；同帧内坐标不得重复。
- **首帧每个点必须 `lock` 一名不同成员**（点位卡只有首张写姓名）。
- 后续帧的点可以 `lock` 成员、要求 `role`、两者皆有或皆无。
- `lock` 必须引用已定义成员，否则整单 422；未定义的角色不报错，只会导致无解。

## 求解目标（排序依据）

在所有满足约束（每帧一人一点、锁定/角色、单步上限）的方案中，按字典序依次最小化：

1. **最大单步** `max_step`：所有成员、所有相邻帧之间的最大移动距离；
2. **步距总和** `total_distance`：所有相邻帧移动距离之和；
3. **成员编号序列** `member_sequence`：按（帧, 帧内点位输入顺序）连接的成员编号序列，
   按 Unicode 码点比较（如 `"Zoe"` < `"ada"`，因为 `Z` 的码点小于 `a`）。

求解器为整数规划（CBC），跨全部帧联合优化，**不做逐帧贪心**。
浮点目标分层时使用 1e-6 容差，可行性判断使用 1e-9 容差。

## 响应

### 有解（HTTP 200）

```json
{
  "status": "ok",
  "frames": [
    {"frame_index": 0, "line": 2,
     "assignments": [{"point_index": 0, "member": "阿雅"}, {"point_index": 1, "member": "小贝"}]}
  ],
  "transitions": [
    {"from_frame": 0, "to_frame": 1, "from_line": 2, "to_line": 3,
     "steps": [{"member": "阿雅", "from_point": 0, "to_point": 0, "distance": 1.0}],
     "max_step": 1.0, "total_distance": 2.0}
  ],
  "sorting_basis": {
    "objective_order": ["max_step", "total_distance", "member_sequence"],
    "max_step": 1.0,
    "total_distance": 2.0,
    "member_sequence": ["阿雅", "小贝", "阿雅", "小贝"],
    "description": "……"
  }
}
```

`frames` 给出逐帧身份，`transitions[].steps` 给出每步距离，`sorting_basis` 是排序依据。

### 无解（HTTP 200，`status: "infeasible"`）

返回**首个不可行前缀的末帧**及逐点诊断：

```json
{
  "status": "infeasible",
  "first_infeasible_prefix_end": {"frame_index": 1, "line": 3},
  "feasible_frames": 1,
  "points": [
    {"point_index": 0, "x": 5, "y": 0, "lock": null, "role": null,
     "candidates": [],
     "violations": [
       {"member": "阿雅", "code": "STEP_TOO_FAR",
        "detail": "……需 5，超过其单步上限 2",
        "needed_distance": 5.0, "max_step": 2.0, "nearest_point": 0}
     ]}
  ],
  "uncovered_points": [0],
  "unplaceable_members": [],
  "hall_deficiency": null,
  "note": "……"
}
```

每个点对每名相关舞者列出违反的条件：

| code | 含义 |
| --- | --- |
| `LOCKED_TO_OTHER` | 该点锁定给了别人 |
| `MISSING_ROLE` | 缺少该点要求的角色 |
| `STEP_TOO_FAR` | 从上一帧最近的可站立点走来也超过单步上限（成对检查，见 `note`） |

`hall_deficiency` 在资格（锁定/角色）二部图匹配不满时给出亏缺的成员与点位集合。

### 输入错误（HTTP 422）

坏行、重复成员或坐标、未知锁定等会使**整单**被拒绝，返回全部诊断（含行号与字段）：

```json
{
  "detail": {
    "message": "请求未通过校验，整单被拒绝",
    "errors": [
      {"line": 3, "field": "points[0].lock", "code": "UNKNOWN_LOCK",
       "message": "第 3 行锁定了未定义的成员 'nobody'"}
    ]
  }
}
```

| code | 含义 |
| --- | --- |
| `EMPTY_BODY` / `EMPTY_LINE` / `INVALID_JSON` / `INVALID_ENCODING` | 空请求体、空行、坏 JSON、非 UTF-8 |
| `INVALID_FIELD` | 字段类型/取值非法（含多余字段、负 `max_step`、NaN/Infinity） |
| `TOO_FEW_FRAMES` / `TOO_MANY_FRAMES` | 帧数不在 2–10 |
| `POINT_COUNT_MISMATCH` | 某帧点数 ≠ 成员数 |
| `DUPLICATE_MEMBER` / `DUPLICATE_COORDINATES` | 成员编号重复 / 同帧坐标重复 |
| `UNKNOWN_LOCK` | 锁定未定义的成员 |
| `FIRST_FRAME_LOCK_REQUIRED` / `FIRST_FRAME_LOCK_NOT_UNIQUE` | 首帧缺锁定 / 首帧锁定不唯一 |

行号为 NDJSON 中的 1 起行号（第 1 行是成员定义，帧 `f` 在第 `f+2` 行）。

## 模块结构

```
app/
  contracts.py   # 契约：请求行模型、诊断、响应模型（Pydantic）
  parser.py      # 契约诊断：NDJSON 解析与全部 422 校验
  solver.py      # 联合求解：整数规划，三级字典序目标
  witness.py     # 不可行见证：首个不可行前缀 + 逐点诊断 + 霍尔亏缺
  main.py        # API：FastAPI 入口（/solve、/health）
tests/           # pytest：对称误配、未来锚点、并列方案、无解、坏行
scripts/smoke.py # HTTP 冒烟（verify 服务使用，打印可抄写的连续走位）
```

## 说明与限制

- 精确求解，无固定答案、无占位逻辑；成员数建议 ≤ 16（社团规模），帧数 2–10。
- 仅后端 API，无前端、无外部账号依赖；CBC 求解器随镜像安装。
- `GET /health` 供健康检查与冒烟使用。
