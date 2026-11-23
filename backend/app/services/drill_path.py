"""下钻路径领域服务：定义从汇总到明细的多层下钻分析路径。

真实数据流：定义下钻规则（起始指标/下钻维度/跳转目标/默认粒度）
→ 执行下钻：从 aggregation_results 读取汇总数据 → 按维度展开明细
→ 跳转到目标模块的实体详情。

当下钻规则绑定只读共享包（drill_rules.package_id）时，下钻不再读全量事件表，
而是走共享包的授权读取：只返回授权区域和维度内的事件明细，
每次下钻都在 shared_package_reads 留一条请求时/返回时授权状态可查的读记录。
"""
from __future__ import annotations

from typing import Any

from app.database import db
from app.services import shared_package
from app.services.base import BaseModuleService


class DrillPathService(BaseModuleService):
    MODULE = "drill_path"
    ENTITY = "下钻规则"
    OPERATION_TERM = "查询"
    LIST_FIELDS = ["规则名称", "起始指标", "下钻维度", "跳转目标", "默认粒度"]
    REQUIRED_FIELDS = ["规则名称", "起始指标", "下钻维度"]
    STATUS_ORDER = ["草稿", "已启用", "已停用"]
    ACTION_RULES = {"启用规则": "已启用", "停用规则": "已停用"}
    NEGATIVE_ACTIONS = []

    def execute_drill(
        self,
        rule_id: int,
        *,
        credential_token: str | None = None,
        recipient_dept: str | None = None,
        regions: list[str] | None = None,
        dimensions: list[str] | None = None,
        hold_ms: int = 0,
    ) -> tuple[int, dict[str, Any]]:
        """执行下钻：从汇总指标逐层展开到明细数据。

        返回 (HTTP 状态码, 响应体)。绑定共享包的规则会经过授权校验并留读记录。
        """
        rule = db.fetchone("SELECT * FROM drill_rules WHERE id=?", [rule_id])
        if rule is None:
            return 404, {"ok": False, "message": f"下钻规则 {rule_id} 不存在"}

        package_id = rule.get("package_id")
        if package_id:
            return self._drill_via_package(
                rule, int(package_id),
                credential_token=credential_token,
                recipient_dept=recipient_dept,
                regions=regions,
                dimensions=dimensions,
                hold_ms=hold_ms,
            )

        return 200, self._drill_local(rule)

    def _drill_via_package(
        self,
        rule: dict[str, Any],
        package_id: int,
        *,
        credential_token: str | None,
        recipient_dept: str | None,
        regions: list[str] | None,
        dimensions: list[str] | None,
        hold_ms: int,
    ) -> tuple[int, dict[str, Any]]:
        status, payload = shared_package.open_package(
            package_id,
            credential_token=credential_token,
            recipient_dept=recipient_dept,
            regions=regions,
            dimensions=dimensions,
            operation="drill",
            hold_ms=hold_ms,
        )
        payload["rule_id"] = rule["id"]
        payload["rule_name"] = rule["rule_name"]
        payload["drill_dimension"] = rule.get("drill_dimension", "")
        payload["target_module"] = rule.get("target_module", "")
        payload["package_id"] = package_id
        if status == 200:
            payload["layers"] = [
                {
                    "layer": 1,
                    "name": f"汇总: {rule.get('start_metric', '')}",
                    "data": payload.get("aggregates", []),
                    "granularity": rule.get("default_granularity", "day"),
                },
                {
                    "layer": 2,
                    "name": f"按{rule.get('drill_dimension', '')}拆解的事件明细",
                    "data": payload.get("events", []),
                    "granularity": "raw",
                },
            ]
        return status, payload

    def _drill_local(self, rule: dict[str, Any]) -> dict[str, Any]:
        """未绑定共享包的旧路径：读取本地聚合与事件表（仅限拥有方内部使用）。"""
        start_metric = rule.get("start_metric", "")
        drill_dim = rule.get("drill_dimension", "")
        target = rule.get("target_module", "")
        granularity = rule.get("default_granularity", "day")

        layers: list[dict[str, Any]] = []

        # Layer 1: 起始指标汇总值
        metric = db.fetchone("SELECT * FROM metrics WHERE metric_name LIKE ?", [f"%{start_metric}%"])
        if metric:
            values = db.fetchall(
                "SELECT * FROM metric_values WHERE metric_id=? ORDER BY snapshot_time DESC LIMIT 5",
                [metric["id"]],
            )
            layers.append({
                "layer": 1,
                "name": f"指标: {start_metric}",
                "data": [{"time": v["snapshot_time"], "value": v["snapshot_value"]} for v in values],
                "granularity": granularity,
            })
        else:
            layers.append({"layer": 1, "name": start_metric, "data": [], "granularity": granularity})

        # Layer 2: 按下钻维度拆解聚合结果
        agg_results = db.fetchall(
            "SELECT * FROM aggregation_results WHERE dimension_key LIKE ? ORDER BY window_start DESC LIMIT 30",
            [f"%{drill_dim}%"],
        )
        layers.append({
            "layer": 2,
            "name": f"按{drill_dim}拆解",
            "data": [{"dim_key": r["dimension_key"], "value": r["computed_value"],
                      "event_count": r["event_count"]} for r in agg_results],
            "granularity": granularity,
        })

        # Layer 3: 跳转到目标模块的实体详情
        if target in ("events", "event_tracker"):
            target_events = db.fetchall(
                "SELECT * FROM events WHERE event_attrs LIKE ? LIMIT 20",
                [f"%{drill_dim}%"],
            )
            layers.append({
                "layer": 3,
                "name": f"明细事件: {target}",
                "data": [{"event_name": e["event_name"], "occurred_at": e["occurred_at"],
                          "attrs": e["event_attrs"]} for e in target_events],
                "granularity": "raw",
            })
        elif target:
            target_data = db.fetchall(f"SELECT * FROM {target} LIMIT 20")
            layers.append({
                "layer": 3,
                "name": f"跳转目标: {target}",
                "data": target_data,
                "granularity": "raw",
            })

        return {
            "ok": True,
            "rule_name": rule.get("rule_name", ""),
            "start_metric": start_metric,
            "drill_dimension": drill_dim,
            "layers": layers,
        }

    def domain_snapshot(self) -> dict[str, Any]:
        snapshot = super().domain_snapshot()
        snapshot["total_rules"] = db.count("drill_rules")
        snapshot["enabled_rules"] = db.count("drill_rules", "status='已启用'")
        snapshot["package_rules"] = db.count("drill_rules", "package_id IS NOT NULL")
        return snapshot
