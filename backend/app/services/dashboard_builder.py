"""仪表盘构建器领域服务：构建和配置数据可视化仪表盘。

真实数据流：配置仪表盘（图表配置/刷新间隔/数据范围）
→ 按刷新间隔从 aggregation_results 和 metric_values 拉取数据
→ 拼装为仪表盘数据 → 写入 dashboards 表。

跨部门核对场景：接收方部门打开仪表盘时，共享包图表不再直读全量明细，
而是先用「只读共享包」筛出该部门当前可用的聚合凭据，再把凭据用于下钻。
"""
from __future__ import annotations

import json
from typing import Any

from app.database import db
from app.services import shared_package
from app.services.base import BaseModuleService


class DashboardBuilderService(BaseModuleService):
    MODULE = "dashboard_builder"
    ENTITY = "仪表盘"
    OPERATION_TERM = "关联"
    LIST_FIELDS = ["仪表盘名称", "包含图表", "刷新间隔", "数据范围", "共享状态"]
    REQUIRED_FIELDS = ["仪表盘名称", "包含图表", "刷新间隔"]
    STATUS_ORDER = ["草稿", "已发布", "已归档"]
    ACTION_RULES = {"发布面板": "已发布", "归档面板": "已归档"}
    NEGATIVE_ACTIONS = []

    def recipient_credentials(self, recipient_dept: str) -> dict[str, Any]:
        """接收方视角：列出共享包筛出的可用聚合凭据（不含任何样本明细）。"""
        available = shared_package.available_for(recipient_dept)
        return {
            "ok": True,
            "recipient_dept": recipient_dept,
            "package_count": len(available),
            "packages": available,
        }

    def dashboard_data(
        self,
        dashboard_id: int,
        *,
        recipient_dept: str | None = None,
        credential_token: str | None = None,
        hold_ms: int = 0,
    ) -> tuple[int, dict[str, Any]]:
        """组装仪表盘的实时数据，按接收方可用凭据过滤共享包范围。"""
        dashboard = db.fetchone("SELECT * FROM dashboards WHERE id=?", [dashboard_id])
        if dashboard is None:
            return 404, {"ok": False, "message": "仪表盘不存在"}
        return 200, self._build_dashboard_data(
            dashboard,
            recipient_dept=recipient_dept,
            credential_token=credential_token,
            hold_ms=hold_ms,
        )

    def _build_dashboard_data(
        self,
        dashboard: dict[str, Any],
        *,
        recipient_dept: str | None,
        credential_token: str | None,
        hold_ms: int,
    ) -> dict[str, Any]:
        chart_configs_raw = dashboard.get("chart_configs", "[]")
        try:
            charts = json.loads(chart_configs_raw) if isinstance(chart_configs_raw, str) else chart_configs_raw
        except (json.JSONDecodeError, TypeError):
            charts = []

        data_scope = dashboard.get("data_scope", "")

        # 接收方：先用共享包筛选可用的聚合凭据。
        credentials = shared_package.available_for(recipient_dept or "")
        if not credentials and recipient_dept:
            return {
                "ok": True,
                "dashboard_name": dashboard["dashboard_name"],
                "refresh_interval": dashboard.get("refresh_interval_seconds", 30),
                "data_scope": data_scope,
                "shared_packages": [],
                "charts": [],
                "message": "当前部门没有可用的只读共享包凭据",
            }

        selected: dict[str, Any] | None = None
        if credential_token:
            selected = next(
                (item for item in credentials if item["credential"]["token"] == credential_token), None)
        elif credentials:
            selected = credentials[0]

        chart_data: list[dict[str, Any]] = []
        for chart in charts:
            chart_type = chart.get("type", "line")
            chart_title = chart.get("title", "图表")
            module = chart.get("module", "events")
            package_id = chart.get("package_id")

            if module == "shared_package" or package_id:
                target = next(
                    (item for item in credentials if item["package"]["id"] == package_id),
                    selected,
                )
                if target is None:
                    chart_data.append({
                        "chart_type": chart_type,
                        "title": chart_title,
                        "data": [],
                        "message": "无可用共享包凭据",
                    })
                    continue
                status, drill = shared_package.open_package(
                    target["package"]["id"],
                    credential_token=target["credential"]["token"],
                    regions=chart.get("regions"),
                    dimensions=chart.get("dimensions"),
                    operation="drill",
                    hold_ms=hold_ms,
                )
                if status != 200:
                    chart_data.append({
                        "chart_type": chart_type,
                        "title": chart_title,
                        "data": [],
                        "read_id": drill.get("read_id"),
                        "message": drill.get("message", "共享包读取未交付"),
                        "delivered_count": 0,
                    })
                    continue
                chart_data.append({
                    "chart_type": chart_type,
                    "title": chart_title,
                    "read_id": drill["read_id"],
                    "package_id": target["package"]["id"],
                    "credential": {
                        "id": target["credential"]["id"],
                        "grant_id": target["credential"]["grant_id"],
                        "grant_version": target["credential"]["grant_version"],
                        "recipient_dept": target["credential"]["recipient_dept"],
                    },
                    "scope": drill["scope"],
                    "delivered_count": drill["delivered_count"],
                    "data": drill["aggregates"],
                    "events": drill.get("events", []),
                })
            elif module == "aggregation":
                rows = db.fetchall(
                    "SELECT * FROM aggregation_results ORDER BY window_start DESC LIMIT 30"
                )
                chart_data.append({"chart_type": chart_type, "title": chart_title, "data": rows})
            elif module == "metrics":
                rows = db.fetchall(
                    "SELECT mv.snapshot_value, mv.snapshot_time, m.metric_name "
                    "FROM metric_values mv JOIN metrics m ON mv.metric_id=m.id "
                    "ORDER BY mv.snapshot_time DESC LIMIT 30"
                )
                chart_data.append({"chart_type": chart_type, "title": chart_title, "data": rows})
            else:
                rows = db.fetchall("SELECT event_name as name, COUNT(*) as cnt FROM events GROUP BY event_name LIMIT 20")
                chart_data.append({"chart_type": chart_type, "title": chart_title, "data": rows})

        if not charts:
            shared_charts = [
                {
                    "chart_type": "bar",
                    "title": f"{item['package']['package_name']}（授权区域汇总）",
                    "package_id": item["package"]["id"],
                    "credential": {
                        "id": item["credential"]["id"],
                        "grant_id": item["credential"]["grant_id"],
                        "grant_version": item["credential"]["grant_version"],
                        "recipient_dept": item["credential"]["recipient_dept"],
                    },
                    "data": item["aggregates"],
                }
                for item in credentials
            ]
            chart_data = shared_charts

        return {
            "ok": True,
            "dashboard_name": dashboard["dashboard_name"],
            "refresh_interval": dashboard.get("refresh_interval_seconds", 30),
            "data_scope": data_scope,
            "recipient_dept": recipient_dept or "",
            "shared_packages": [
                {
                    "package_id": item["package"]["id"],
                    "package_name": item["package"]["package_name"],
                    "owner_dept": item["package"]["owner_dept"],
                    "scope": item["scope"],
                    "credential_id": item["credential"]["id"],
                    "grant_id": item["credential"]["grant_id"],
                    "grant_version": item["credential"]["grant_version"],
                }
                for item in credentials
            ],
            "charts": chart_data,
        }

    def publish(self, dashboard_id: int) -> dict[str, Any]:
        """发布仪表盘。"""
        db.update("dashboards", {"status": "已发布"}, "id=?", [dashboard_id])
        status, data = self.dashboard_data(dashboard_id)
        return data

    def domain_snapshot(self) -> dict[str, Any]:
        snapshot = super().domain_snapshot()
        snapshot["total_dashboards"] = db.count("dashboards")
        snapshot["published"] = db.count("dashboards", "status='已发布'")
        return snapshot
