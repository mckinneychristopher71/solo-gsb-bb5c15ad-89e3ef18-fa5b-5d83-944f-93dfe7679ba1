# 陶艺工作室共窑排程 API

小型陶艺工作室的「共窑」装窑排程服务：提交 1～14 件器物（唯一编号、正整数体积、
允许烧成温度闭区间）与单窑容量，使用**位掩码动态规划**求出覆盖全部器物的
**最少窑次**方案，并在窑次相同时选择**整体字典序最小**的方案。

技术栈：Python 3.12 · FastAPI · SQLite · Uvicorn。

## 排程规则

- 同一窑的器物必须同时满足：
  1. **总体积 ≤ 单窑容量**；
  2. 各器物温度区间存在**公共交集**（`max(下界) ≤ min(上界)`）；
  3. 该窑烧成温度取交集下界，即 `max(各器物 temp_low)`。
- 目标：窑次数最少。
- 窑次相同时的字典序规则：
  - 每个窑内器物编号**升序**排列；
  - 再将各窑按其编号序列排序（序列比较按标准字符串字典序，前缀视为更小）；
  - 在所有最少窑次方案中，取排序后整体字典序最小者。
- 任何**单件**器物体积超过单窑容量时无法烧成，返回明确错误
  （HTTP 400，错误码 `ITEM_EXCEEDS_CAPACITY`）。

## 单条命令启动

需要 Docker 20.10+ 与 Docker Compose v2：

```bash
docker compose up --build -d
```

容器启动时会自动：

1. 安装 / 校验 Python 依赖（`requirements.txt`）；
2. 初始化 SQLite 数据库（建表，幂等）；
3. 启动 Uvicorn 服务。

停止：`docker compose down`（加 `-v` 可同时删除数据库卷）。

## 配置

均为可选项，通过环境变量配置（见 `docker-compose.yml`）：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `KILN_API_PORT` | `8000` | 宿主机映射端口 |
| `KILN_DB_PATH` | `/data/kiln.db` | 容器内 SQLite 数据库文件路径 |
| `UVICORN_HOST` | `0.0.0.0` | 服务监听地址 |
| `UVICORN_PORT` | `8000` | 容器内监听端口 |

示例：宿主机改用 18000 端口 —— `KILN_API_PORT=18000 docker compose up --build -d`。
数据库通过命名卷 `kiln-data` 持久化于 `/data`。

## API 地址与端口

默认地址：**http://localhost:8000**

- 交互式文档（Swagger UI）：http://localhost:8000/docs
- OpenAPI 规范：http://localhost:8000/openapi.json
- 健康检查：`GET http://localhost:8000/health`

### 1. 创建排程方案

`POST http://localhost:8000/plans`

请求体：

```json
{
  "capacity": 10,
  "items": [
    {"item_id": "p1", "volume": 5, "temp_low": 100, "temp_high": 200},
    {"item_id": "p2", "volume": 6, "temp_low": 120, "temp_high": 220},
    {"item_id": "p3", "volume": 4, "temp_low": 150, "temp_high": 300},
    {"item_id": "p4", "volume": 5, "temp_low": 1000, "temp_high": 1100}
  ]
}
```

字段约束：`items` 数量 1～14；`item_id` 为非空且唯一的字符串；`volume` 与
`capacity` 为正整数；`temp_low ≤ temp_high`（温度为整数闭区间）。

成功响应 `201 Created`：

```json
{
  "id": "9c05e113e3e0430eb552a0bd01036dbb",
  "capacity": 10,
  "item_count": 4,
  "kiln_count": 3,
  "items": [ ... 原样回显 ... ],
  "kilns": [
    {"index": 1, "item_ids": ["p1"],        "total_volume": 5,  "firing_temp": 100},
    {"index": 2, "item_ids": ["p2", "p3"],  "total_volume": 10, "firing_temp": 150},
    {"index": 3, "item_ids": ["p4"],        "total_volume": 5,  "firing_temp": 1000}
  ],
  "created_at": "2026-09-24T05:34:51.026381+00:00"
}
```

错误情况：

- `400` `ITEM_EXCEEDS_CAPACITY`：单件器物超过单窑容量，响应体含全部超容器物编号；
- `422`：请求参数非法（编号重复 / 区间非法 / 数量超限 / 非正整数等）。

### 2. 按 ID 读取方案

`GET http://localhost:8000/plans/{id}`

返回与创建接口相同的完整方案；方案不存在时返回 `404`（错误码 `PLAN_NOT_FOUND`）。

curl 示例：

```bash
curl -s http://localhost:8000/plans/9c05e113e3e0430eb552a0bd01036dbb
```

### 3. 反推满足目标窑次的最小容量

`POST http://localhost:8000/plans/{id}/capacity-target`

针对**已保存**的方案，给定希望达到的目标窑次，反推满足该目标所需的**最小单窑容量**。
计算一律以原方案保存的器物体积与温度区间为准（临时重算，**不落库、不改写原方案**）：

- 在 **最大单件体积 ～ 器物总体积** 的整数容量区间内二分查找；
- 每个候选容量都沿用与创建接口完全相同的求解器：同窑温区交集规则、
  最少窑次 DP，以及窑次相同时的整体字典序排序规则；
- 返回满足「最少窑次 ≤ 目标窑次」的**最小容量**、该容量下的**实际窑次**
  与对应**装窑明细**；
- 若容量放宽到总体积（此时仅受温度区间约束）仍无法达到目标，返回明确的
  不可达错误（HTTP 400，错误码 `TARGET_UNREACHABLE`）。

请求体：

```json
{
  "target_kiln_count": 2
}
```

`target_kiln_count` 为正整数，取值范围 **1～原方案器物数**；超过上界返回
`422`（错误码 `TARGET_OUT_OF_RANGE`）。

以上面创建的 4 件器物方案（原容量 10、3 窑）为例，`curl` 调用：

```bash
curl -s -X POST http://localhost:8000/plans/9c05e113e3e0430eb552a0bd01036dbb/capacity-target \
  -H "Content-Type: application/json" \
  -d '{"target_kiln_count": 2}'
```

成功响应 `200 OK`：

```json
{
  "plan_id": "9c05e113e3e0430eb552a0bd01036dbb",
  "target_kiln_count": 2,
  "capacity": 15,
  "item_count": 4,
  "kiln_count": 2,
  "kilns": [
    {"index": 1, "item_ids": ["p1", "p2", "p3"], "total_volume": 15, "firing_temp": 150},
    {"index": 2, "item_ids": ["p4"],          "total_volume": 5,  "firing_temp": 1000}
  ]
}
```

即容量 15 时 p1/p2/p3 可同窑（总体积 15、温度交集 150℃），p4 温区
（1000～1100℃）与其无交集必须单开一窑；容量 14 及以下无法把 p1/p2/p3
装入同窑，故 **15 即满足 2 窑目标的最小容量**。

不可达示例（同一方案要求 1 窑）：

```bash
curl -s -X POST http://localhost:8000/plans/9c05e113e3e0430eb552a0bd01036dbb/capacity-target \
  -H "Content-Type: application/json" \
  -d '{"target_kiln_count": 1}'
```

```json
{
  "error": "TARGET_UNREACHABLE",
  "message": "目标窑次 1 不可达：即使单窑容量放宽到总体积 20（温度区间约束成为唯一限制），最少仍需 2 窑"
}
```

错误情况：

- `400` `TARGET_UNREACHABLE`：总体积容量下最少窑次仍超过目标（温度区间无法兼容）；
- `404` `PLAN_NOT_FOUND`：方案不存在；
- `422` `TARGET_OUT_OF_RANGE`：目标窑次超出 1～原方案器物数；
- `422`：请求体非法（缺字段 / 非正整数等）。

## 本地开发（不使用 Docker）

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
KILN_DB_PATH=./kiln.db uvicorn app.main:app --reload
```

运行求解器测试（无需第三方依赖，含 300 组随机用例的暴力枚举交叉验证）：

```bash
python3 tests/test_solver.py
```

## 算法说明（n ≤ 14）

1. 枚举全部 `2^n` 个器物子集，用「去掉最低位 + 一件」递推每个子集的
   总体积与温度交集，标记可成窑的子集；
2. 位掩码 DP：`dp[0]=0`，
   `dp[mask] = 1 + min(dp[mask ^ g])`（`g` 枚举 `mask` 的可行非空子集）；
3. 贪心重建字典序最小方案：每一步在剩余器物中，枚举「包含剩余最小编号器物」的
   所有可行子集，选出取出后剩余器物仍能在剩余窑次内装完的、
   器物编号序列字典序最小的子集作为下一窑。

n = 14 时子集总数 16 384，典型耗时在 1 秒以内。

## 目录结构

```
app/
  solver.py     # 位掩码 DP + 字典序重建（纯函数，无框架依赖）
  database.py   # SQLite 初始化与读写
  schemas.py    # Pydantic 请求/响应模型
  main.py       # FastAPI 路由
tests/
  test_solver.py
entrypoint.sh   # 安装依赖 → 初始化数据库 → 启动服务
Dockerfile
docker-compose.yml
requirements.txt
```
