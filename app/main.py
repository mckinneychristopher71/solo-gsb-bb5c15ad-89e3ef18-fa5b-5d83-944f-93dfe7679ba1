"""陶艺工作室共窑排程 API。

POST /plans                              提交器物与容量，求解、保存并返回方案
GET  /plans/{plan_id}                    按 ID 读取已保存方案
POST /plans/{plan_id}/capacity-target    基于已保存方案反推满足目标窑次的最小容量
GET  /health                             健康检查
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from . import database
from .schemas import (
    CapacityTargetIn,
    CapacityTargetOut,
    ErrorOut,
    PlanCreate,
    PlanOut,
)
from .solver import (
    CapacityExceededError,
    TargetUnreachableError,
    Ware,
    solve,
    solve_capacity_target,
)

app = FastAPI(
    title="陶艺工作室共窑排程 API",
    version="1.0.0",
    description="基于位掩码动态规划的最少窑次装窑排程服务（器物 1~14 件）",
)


@app.on_event("startup")
def _startup() -> None:
    database.init_db()


@app.exception_handler(CapacityExceededError)
def _capacity_exceeded_handler(_: Request, exc: CapacityExceededError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"error": "ITEM_EXCEEDS_CAPACITY", "message": str(exc)},
    )


@app.exception_handler(TargetUnreachableError)
def _target_unreachable_handler(_: Request, exc: TargetUnreachableError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"error": "TARGET_UNREACHABLE", "message": str(exc)},
    )


@app.get("/health", tags=["system"])
def health() -> dict:
    return {"status": "ok"}


@app.post(
    "/plans",
    response_model=PlanOut,
    status_code=201,
    tags=["plans"],
    responses={400: {"model": ErrorOut, "description": "单件器物超过单窑容量"}},
    summary="创建共窑排程方案",
)
def create_plan(payload: PlanCreate) -> PlanOut:
    wares = [
        Ware(
            item_id=it.item_id,
            volume=it.volume,
            temp_low=it.temp_low,
            temp_high=it.temp_high,
        )
        for it in payload.items
    ]

    # 单件超容量时 solver 抛出 CapacityExceededError，由全局处理器返回 400。
    kilns = solve(wares, payload.capacity)

    plan = {
        "id": uuid.uuid4().hex,
        "capacity": payload.capacity,
        "item_count": len(wares),
        "kiln_count": len(kilns),
        "items": [it.model_dump() for it in payload.items],
        "kilns": [
            {
                "index": idx,
                "item_ids": list(k.item_ids),
                "total_volume": k.total_volume,
                "firing_temp": k.firing_temp,
            }
            for idx, k in enumerate(kilns, start=1)
        ],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    database.save_plan(plan)
    return PlanOut(**plan)


@app.get(
    "/plans/{plan_id}",
    response_model=PlanOut,
    tags=["plans"],
    responses={404: {"model": ErrorOut, "description": "方案不存在"}},
    summary="按 ID 读取排程方案",
)
def read_plan(plan_id: str) -> PlanOut:
    plan = database.get_plan(plan_id)
    if plan is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "PLAN_NOT_FOUND",
                "message": f"未找到排程方案: {plan_id}",
            },
        )
    return PlanOut(**plan)


@app.post(
    "/plans/{plan_id}/capacity-target",
    response_model=CapacityTargetOut,
    tags=["plans"],
    responses={
        400: {"model": ErrorOut, "description": "目标窑次在温度约束下不可达"},
        404: {"model": ErrorOut, "description": "方案不存在"},
    },
    summary="反推满足目标窑次的最小单窑容量",
)
def capacity_target(plan_id: str, payload: CapacityTargetIn) -> CapacityTargetOut:
    plan = database.get_plan(plan_id)
    if plan is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "PLAN_NOT_FOUND",
                "message": f"未找到排程方案: {plan_id}",
            },
        )

    item_count = plan["item_count"]
    target = payload.target_kiln_count
    if target > item_count:
        # 上界取决于已保存方案的器物数，无法在 Pydantic 层静态校验。
        raise HTTPException(
            status_code=422,
            detail={
                "error": "TARGET_OUT_OF_RANGE",
                "message": (
                    f"目标窑次必须在 1～{item_count} 之间（原方案器物数），"
                    f"收到: {target}"
                ),
            },
        )

    # 以原方案保存的器物体积与温度区间为准，临时计算，不改写原方案。
    wares = [
        Ware(
            item_id=it["item_id"],
            volume=it["volume"],
            temp_low=it["temp_low"],
            temp_high=it["temp_high"],
        )
        for it in plan["items"]
    ]
    result = solve_capacity_target(wares, target)

    return CapacityTargetOut(
        plan_id=plan_id,
        target_kiln_count=target,
        capacity=result.capacity,
        item_count=item_count,
        kiln_count=result.kiln_count,
        kilns=[
            {
                "index": idx,
                "item_ids": list(k.item_ids),
                "total_volume": k.total_volume,
                "firing_temp": k.firing_temp,
            }
            for idx, k in enumerate(result.kilns, start=1)
        ],
    )
