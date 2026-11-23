"""共享包消费入口：下钻路径执行、仪表盘按接收方凭据取数。

放在通用资源路由之前注册，避免 /api/{module}/{entry_id} 抢先匹配。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.services import shared_package
from app.services import service_for

router = APIRouter(prefix="/api", tags=["共享包消费"])


class DrillPayload(BaseModel):
    credential_token: str | None = None
    recipient_dept: str | None = None
    regions: list[str] | None = None
    dimensions: list[str] | None = None
    hold_ms: int = 0


def _respond(status: int, body: dict[str, Any]) -> dict[str, Any]:
    if status >= 400:
        raise HTTPException(status_code=status, detail=body)
    return body


@router.post("/drill-path/{rule_id}/execute")
def execute_drill_rule(rule_id: int, payload: DrillPayload | None = None) -> dict[str, Any]:
    """执行下钻规则；绑定共享包的规则只返回授权范围内的事件明细。"""
    payload = payload or DrillPayload()
    service = service_for("drill_path")
    status, body = service.execute_drill(
        rule_id,
        credential_token=payload.credential_token,
        recipient_dept=payload.recipient_dept,
        regions=payload.regions,
        dimensions=payload.dimensions,
        hold_ms=payload.hold_ms,
    )
    return _respond(status, body)


@router.get("/dashboard-builder/credentials")
def recipient_credentials(
    recipient_dept: str = Query(..., description="接收部门"),
) -> dict[str, Any]:
    """接收方可用凭据：仪表盘构建器用来筛选共享包，不含样本明细。"""
    return service_for("dashboard_builder").recipient_credentials(recipient_dept)


@router.get("/dashboard-builder/{dashboard_id}/data")
def dashboard_data(
    dashboard_id: int,
    recipient_dept: str | None = Query(default=None),
    credential_token: str | None = Header(default=None, alias="X-Package-Credential"),
    hold_ms: int = Query(default=0, ge=0, le=15_000),
) -> dict[str, Any]:
    """仪表盘取数：共享包图表先筛出接收方凭据，再用凭据下钻。"""
    status, body = service_for("dashboard_builder").dashboard_data(
        dashboard_id,
        recipient_dept=recipient_dept,
        credential_token=credential_token,
        hold_ms=hold_ms,
    )
    return _respond(status, body)
