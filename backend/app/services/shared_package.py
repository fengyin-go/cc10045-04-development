"""只读共享包领域服务：供其他部门核对聚合明细的授权与读取。

核心约束：
  1. 共享包由拥有方登记，包含若干区域/维度的聚合明细样本；
  2. 接收部门必须先领取授权，授权签发只读凭据（聚合凭据）；
  3. 打开/下钻只返回「包范围 ∩ 授权范围 ∩ 请求范围」内的样本，
     组外样本（例如未授权的西北）任何时刻都不会出现在返回或核对记录里；
  4. 每次读取都留一条读记录：请求时与返回时分别记录凭据/授权的状态和版本；
  5. 权限撤销后发起的新读取立即失效；交错读取在返回前复查，
     已撤销则本次不交付，拥有方仍可按读记录与当时版本核对已交付内容。
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
import uuid
from typing import Any

from app.database import db

PACKAGE_STATUSES = ("草稿", "已共享", "已停用")
GRANT_ACTIVE = "已授权"
GRANT_REVOKED = "已撤销"
CREDENTIAL_ACTIVE = "有效"
CREDENTIAL_REVOKED = "已撤销"

# 交错测试窗口上限（毫秒）：请求通过授权检查后、返回前等待，便于外部并发撤销。
MAX_HOLD_MS = 15_000


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def _now() -> str:
    """毫秒级时间，保证请求/撤销/返回的先后在审计记录里可比较。"""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (list, dict)):
        return value
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _sha256(rows: list[dict[str, Any]]) -> str:
    canonical = [
        [row.get("id"), row.get("sample_key"), row.get("region"),
         row.get("dimension_key"), row.get("event_name"), row.get("measure"),
         row.get("occurred_at")]
        for row in rows
    ]
    return hashlib.sha256(_dumps(canonical).encode("utf-8")).hexdigest()


def _package(package_id: int) -> dict[str, Any] | None:
    return db.fetchone("SELECT * FROM shared_packages WHERE id=?", [package_id])


def _grant(grant_id: int | None) -> dict[str, Any] | None:
    if grant_id is None:
        return None
    return db.fetchone("SELECT * FROM shared_package_grants WHERE id=?", [grant_id])


def _credential(credential_id: int | None) -> dict[str, Any] | None:
    if credential_id is None:
        return None
    return db.fetchone("SELECT * FROM shared_package_credentials WHERE id=?", [credential_id])


def _serialize_package(package: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": package["id"],
        "package_name": package["package_name"],
        "description": package.get("description", ""),
        "owner_dept": package["owner_dept"],
        "source_module": package.get("source_module", ""),
        "source_ref": package.get("source_ref", ""),
        "data_scope": _loads(package.get("data_scope"), []),
        "dimensions": _loads(package.get("dimensions"), []),
        "data_version": package.get("data_version", 1),
        "status": package.get("status", "草稿"),
        "created_at": package.get("created_at", ""),
    }


def _serialize_grant(grant: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": grant["id"],
        "package_id": grant["package_id"],
        "recipient_dept": grant["recipient_dept"],
        "regions": _loads(grant.get("regions"), []),
        "dimensions": _loads(grant.get("dimensions"), []),
        "grant_version": grant.get("grant_version", 1),
        "status": grant.get("status", GRANT_ACTIVE),
        "issued_by": grant.get("issued_by", ""),
        "issued_at": grant.get("issued_at", ""),
        "revoked_by": grant.get("revoked_by"),
        "revoked_at": grant.get("revoked_at"),
    }


def _serialize_credential(credential: dict[str, Any], *, reveal_token: bool = True) -> dict[str, Any]:
    data = {
        "id": credential["id"],
        "package_id": credential["package_id"],
        "grant_id": credential["grant_id"],
        "recipient_dept": credential["recipient_dept"],
        "regions": _loads(credential.get("regions"), []),
        "dimensions": _loads(credential.get("dimensions"), []),
        "grant_version": credential.get("grant_version", 1),
        "status": credential.get("status", CREDENTIAL_ACTIVE),
        "issued_at": credential.get("issued_at", ""),
        "revoked_at": credential.get("revoked_at"),
    }
    if reveal_token:
        data["token"] = credential["token"]
    else:
        data["token_masked"] = "spk_****" + str(credential["token"])[-4:]
    return data


def _serialize_sample(sample: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": sample["id"],
        "sample_key": sample["sample_key"],
        "region": sample["region"],
        "dimension_key": sample["dimension_key"],
        "event_name": sample["event_name"],
        "measure": sample["measure"],
        "occurred_at": sample["occurred_at"],
        "attrs": _loads(sample.get("attrs"), {}),
        "data_version": sample.get("data_version", 1),
    }


def _serialize_read(read: dict[str, Any]) -> dict[str, Any]:
    return {
        "read_id": read["read_id"],
        "package_id": read["package_id"],
        "grant_id": read.get("grant_id"),
        "credential_id": read.get("credential_id"),
        "recipient_dept": read.get("recipient_dept", ""),
        "operation": read.get("operation", "open"),
        "request_time": read.get("request_time"),
        "request_auth_status": read.get("request_auth_status"),
        "request_grant_version": read.get("request_grant_version"),
        "request_package_version": read.get("request_package_version"),
        "request_scope": _loads(read.get("request_scope"), []),
        "return_time": read.get("return_time"),
        "return_auth_status": read.get("return_auth_status"),
        "return_grant_version": read.get("return_grant_version"),
        "return_package_version": read.get("return_package_version"),
        "return_scope": _loads(read.get("return_scope"), []),
        "outcome": read.get("outcome"),
        "delivered_count": read.get("delivered_count", 0),
        "delivered_regions": _loads(read.get("delivered_regions"), []),
        "delivered_sample_ids": _loads(read.get("delivered_sample_ids"), []),
        "delivered_hash": read.get("delivered_hash", ""),
        "delivered_rows": _loads(read.get("delivered_rows"), []),
    }


# ---------------------------------------------------------------------------
# 授权状态判定
# ---------------------------------------------------------------------------

def _authorization_state(
    package: dict[str, Any],
    credential: dict[str, Any] | None,
    grant: dict[str, Any] | None,
) -> dict[str, Any]:
    """计算「此刻」的授权状态。请求时和返回时各算一次。"""
    package_version = package.get("data_version", 1)
    package_status = package.get("status", "草稿")
    state: dict[str, Any] = {
        "authorized": False,
        "auth_status": "未授权",
        "credential_status": credential.get("status") if credential else "未领取",
        "grant_status": grant.get("status") if grant else "未授权",
        "grant_version": grant.get("grant_version") if grant else None,
        "package_version": package_version,
        "package_status": package_status,
        "regions": [],
        "dimensions": [],
        "reason": "",
    }

    if package_status in ("已停用", "已归档"):
        state["reason"] = "共享包已停用"
        return state
    if credential is None:
        state["reason"] = "未领取授权"
        return state
    if grant is None:
        state["reason"] = "授权不存在"
        state["credential_status"] = credential.get("status", "")
        return state
    if credential.get("status") != CREDENTIAL_ACTIVE:
        state["reason"] = "凭据已撤销"
        return state
    if grant.get("status") != GRANT_ACTIVE:
        state["reason"] = "授权已撤销"
        return state
    if credential.get("grant_version") != grant.get("grant_version"):
        state["reason"] = "授权版本已变更，请重新领取"
        return state

    package_regions = set(_loads(package.get("data_scope"), []))
    grant_regions = set(_loads(grant.get("regions"), []))
    credential_regions = set(_loads(credential.get("regions"), []))
    regions = sorted(package_regions & grant_regions & credential_regions)
    if not regions:
        state["reason"] = "授权区域不在包范围内"
        return state

    package_dims = set(_loads(package.get("dimensions"), []))
    grant_dims = set(_loads(grant.get("dimensions"), [])) or package_dims
    credential_dims = set(_loads(credential.get("dimensions"), [])) or grant_dims
    dimensions = sorted(package_dims & grant_dims & credential_dims)

    state.update({
        "authorized": True,
        "auth_status": "已授权",
        "regions": regions,
        "dimensions": dimensions,
        "reason": "ok",
    })
    return state


def _resolve_credential(
    package_id: int,
    *,
    credential_token: str | None = None,
    recipient_dept: str | None = None,
    actor_dept: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str]:
    """解析本次请求使用的凭据和授权。返回 (credential, grant, 失败原因)。"""
    dept = (recipient_dept or actor_dept or "").strip()

    if credential_token:
        credential = db.fetchone(
            "SELECT * FROM shared_package_credentials WHERE token=?", [credential_token.strip()])
        if credential is None:
            return None, None, "凭据无效"
        if credential["package_id"] != package_id:
            return None, None, "凭据不属于该共享包"
        grant = _grant(credential.get("grant_id"))
        return credential, grant, ""

    if dept:
        row = db.fetchone(
            "SELECT * FROM shared_package_credentials "
            "WHERE package_id=? AND recipient_dept=? AND status=? "
            "ORDER BY id DESC LIMIT 1",
            [package_id, dept, CREDENTIAL_ACTIVE],
        )
        if row is None:
            # 已撤销的凭据也要能定位到，用于记录“撤销后新读取失效”。
            row = db.fetchone(
                "SELECT * FROM shared_package_credentials "
                "WHERE package_id=? AND recipient_dept=? ORDER BY id DESC LIMIT 1",
                [package_id, dept],
            )
        if row is None:
            return None, None, "未领取授权"
        grant = _grant(row.get("grant_id"))
        return row, grant, ""

    return None, None, "缺少凭据或接收部门"


# ---------------------------------------------------------------------------
# 读记录
# ---------------------------------------------------------------------------

def _start_read(
    package: dict[str, Any],
    request_state: dict[str, Any],
    *,
    credential: dict[str, Any] | None,
    grant: dict[str, Any] | None,
    operation: str,
    recipient_dept: str,
) -> str:
    read_id = f"read_{uuid.uuid4().hex[:16]}"
    db.insert("shared_package_reads", {
        "read_id": read_id,
        "package_id": package["id"],
        "grant_id": grant["id"] if grant else None,
        "credential_id": credential["id"] if credential else None,
        "credential_token": credential["token"] if credential else None,
        "recipient_dept": recipient_dept,
        "operation": operation,
        "request_time": _now(),
        "request_auth_status": request_state["auth_status"],
        "request_grant_version": request_state["grant_version"],
        "request_package_version": request_state["package_version"],
        "request_scope": _dumps(request_state["regions"]),
        "return_auth_status": "待返回",
        "return_scope": _dumps([]),
        "outcome": "处理中",
        "delivered_count": 0,
        "delivered_regions": _dumps([]),
        "delivered_sample_ids": _dumps([]),
        "delivered_hash": "",
        "delivered_rows": _dumps([]),
    })
    return read_id


def _finish_read(
    read_id: str,
    return_state: dict[str, Any],
    *,
    outcome: str,
    rows: list[dict[str, Any]],
) -> None:
    delivered_regions = sorted({row["region"] for row in rows})
    db.update("shared_package_reads", {
        "return_time": _now(),
        "return_auth_status": return_state["auth_status"],
        "return_grant_version": return_state["grant_version"],
        "return_package_version": return_state["package_version"],
        "return_scope": _dumps(return_state["regions"]),
        "outcome": outcome,
        "delivered_count": len(rows),
        "delivered_regions": _dumps(delivered_regions),
        "delivered_sample_ids": _dumps([row["id"] for row in rows]),
        "delivered_hash": _sha256(rows) if rows else "",
        "delivered_rows": _dumps([_serialize_sample(row) for row in rows]),
    }, "read_id=?", [read_id])


def _deny(
    read_id: str,
    request_state: dict[str, Any],
    *,
    message: str,
    outcome: str = "未授权",
    package: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _finish_read(read_id, request_state, outcome=outcome, rows=[])
    body: dict[str, Any] = {
        "ok": False,
        "read_id": read_id,
        "outcome": outcome,
        "message": message,
        "delivered_count": 0,
        "auth": {
            "request": {
                "time": _read_request_time(read_id),
                "status": request_state["auth_status"],
                "grant_version": request_state["grant_version"],
                "package_version": request_state["package_version"],
                "scope": request_state["regions"],
            },
            "return": {
                "time": _read_return_time(read_id),
                "status": request_state["auth_status"],
                "grant_version": request_state["grant_version"],
                "package_version": request_state["package_version"],
                "scope": request_state["regions"],
            },
        },
    }
    if package is not None:
        body["package_id"] = package["id"]
        body["package_name"] = package["package_name"]
    return body


# ---------------------------------------------------------------------------
# 包管理：登记、授权、撤销、凭据
# ---------------------------------------------------------------------------

def create_package(values: dict[str, Any]) -> dict[str, Any]:
    missing = [key for key in ("package_name", "owner_dept") if not str(values.get(key) or "").strip()]
    if missing:
        return {"ok": False, "message": f"缺少必填字段：{'、'.join(missing)}"}

    regions = values.get("data_scope") or values.get("regions") or []
    if isinstance(regions, str):
        regions = [part.strip() for part in regions.split(",") if part.strip()]
    dimensions = values.get("dimensions") or []
    if isinstance(dimensions, str):
        dimensions = [part.strip() for part in dimensions.split(",") if part.strip()]

    package_id = db.insert("shared_packages", {
        "package_name": str(values["package_name"]).strip(),
        "description": str(values.get("description") or ""),
        "owner_dept": str(values["owner_dept"]).strip(),
        "source_module": str(values.get("source_module") or "drill_path"),
        "source_ref": str(values.get("source_ref") or ""),
        "data_scope": _dumps(regions),
        "dimensions": _dumps(dimensions),
        "data_version": int(values.get("data_version") or 1),
        "status": values.get("status") if values.get("status") in PACKAGE_STATUSES else "已共享",
    })

    samples = values.get("samples") or []
    added = 0
    for index, sample in enumerate(samples, start=1):
        if not isinstance(sample, dict):
            continue
        db.insert("shared_package_samples", {
            "package_id": package_id,
            "sample_key": str(sample.get("sample_key") or f"S{index:03d}"),
            "region": str(sample.get("region") or ""),
            "dimension_key": str(sample.get("dimension_key") or ""),
            "event_name": str(sample.get("event_name") or ""),
            "measure": float(sample.get("measure") or 0.0),
            "occurred_at": str(sample.get("occurred_at") or _now()),
            "attrs": _dumps(sample.get("attrs") or {}),
            "data_version": int(sample.get("data_version") or values.get("data_version") or 1),
        })
        added += 1

    package = _package(package_id)
    return {"ok": True, "message": "只读共享包已登记", "package": serialize_for_owner(package), "samples_added": added}


def issue_grant(
    package_id: int,
    values: dict[str, Any],
    *,
    owner_dept: str | None,
) -> dict[str, Any]:
    package = _package(package_id)
    if package is None:
        return {"ok": False, "message": f"共享包 {package_id} 不存在"}
    if not owner_dept or owner_dept.strip() != package["owner_dept"]:
        return {"ok": False, "message": "只有共享包拥有方可以签发授权", "status": 403}

    recipient = str(values.get("recipient_dept") or "").strip()
    if not recipient:
        return {"ok": False, "message": "缺少接收部门"}

    package_regions = set(_loads(package.get("data_scope"), []))
    regions = values.get("regions") or []
    if isinstance(regions, str):
        regions = [part.strip() for part in regions.split(",") if part.strip()]
    regions = [region for region in regions if region in package_regions]
    if not regions:
        return {"ok": False, "message": "授权区域必须落在共享包的数据范围内"}

    package_dims = set(_loads(package.get("dimensions"), []))
    dimensions = values.get("dimensions") or []
    if isinstance(dimensions, str):
        dimensions = [part.strip() for part in dimensions.split(",") if part.strip()]
    dimensions = [dimension for dimension in dimensions if dimension in package_dims] or sorted(package_dims)

    grant_version = int(values.get("grant_version") or 1)
    # 同一接收部门重新签发时，旧授权与旧凭据立即失效，避免并存多份有效凭据。
    stale = db.fetchall(
        "SELECT * FROM shared_package_grants WHERE package_id=? AND recipient_dept=? AND status=?",
        [package_id, recipient, GRANT_ACTIVE],
    )
    if stale:
        stamp = _now()
        db.execute(
            "UPDATE shared_package_grants SET status=?, revoked_at=?, revoked_by=? "
            "WHERE package_id=? AND recipient_dept=? AND status=?",
            [GRANT_REVOKED, stamp, owner_dept.strip(), package_id, recipient, GRANT_ACTIVE],
        )
        db.execute(
            "UPDATE shared_package_credentials SET status=?, revoked_at=? "
            "WHERE package_id=? AND recipient_dept=? AND status=?",
            [CREDENTIAL_REVOKED, stamp, package_id, recipient, CREDENTIAL_ACTIVE],
        )

    grant_id = db.insert("shared_package_grants", {
        "package_id": package_id,
        "recipient_dept": recipient,
        "regions": _dumps(regions),
        "dimensions": _dumps(dimensions),
        "grant_version": grant_version,
        "status": GRANT_ACTIVE,
        "issued_by": owner_dept.strip(),
    })

    token = values.get("credential_token") or f"spk_{secrets.token_urlsafe(24)}"
    credential_id = db.insert("shared_package_credentials", {
        "token": str(token),
        "package_id": package_id,
        "grant_id": grant_id,
        "recipient_dept": recipient,
        "regions": _dumps(regions),
        "dimensions": _dumps(dimensions),
        "grant_version": grant_version,
        "status": CREDENTIAL_ACTIVE,
    })

    return {
        "ok": True,
        "message": "只读授权已签发",
        "grant": _serialize_grant(_grant(grant_id)),
        "credential": _serialize_credential(_credential(credential_id)),
    }


def revoke_grant(
    package_id: int,
    grant_id: int,
    *,
    owner_dept: str | None,
    revoked_by: str | None = None,
) -> dict[str, Any]:
    package = _package(package_id)
    grant = _grant(grant_id)
    if package is None or grant is None or grant["package_id"] != package_id:
        return {"ok": False, "message": "授权不存在"}
    if not owner_dept or owner_dept.strip() != package["owner_dept"]:
        return {"ok": False, "message": "只有共享包拥有方可以撤销授权", "status": 403}

    revoked_at = _now()
    if grant.get("status") == GRANT_ACTIVE:
        db.update("shared_package_grants", {
            "status": GRANT_REVOKED,
            "revoked_at": revoked_at,
            "revoked_by": (revoked_by or owner_dept).strip(),
        }, "id=?", [grant_id])
        db.execute(
            "UPDATE shared_package_credentials SET status=?, revoked_at=? "
            "WHERE grant_id=? AND status=?",
            [CREDENTIAL_REVOKED, revoked_at, grant_id, CREDENTIAL_ACTIVE],
        )

    credential = db.fetchone(
        "SELECT * FROM shared_package_credentials WHERE grant_id=? ORDER BY id DESC LIMIT 1",
        [grant_id],
    )
    return {
        "ok": True,
        "message": "授权已撤销，凭据立即失效",
        "grant": _serialize_grant(_grant(grant_id)),
        "credential": _serialize_credential(credential) if credential else None,
        "revoked_at": revoked_at,
    }


def list_credentials(
    package_id: int,
    *,
    recipient_dept: str | None = None,
    include_revoked: bool = False,
    reveal_token: bool = True,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM shared_package_credentials WHERE package_id=?"
    params: list[Any] = [package_id]
    if recipient_dept:
        sql += " AND recipient_dept=?"
        params.append(recipient_dept)
    if not include_revoked:
        sql += " AND status=?"
        params.append(CREDENTIAL_ACTIVE)
    sql += " ORDER BY id DESC"
    return [_serialize_credential(row, reveal_token=reveal_token) for row in db.fetchall(sql, params)]


def available_for(recipient_dept: str) -> list[dict[str, Any]]:
    """仪表盘构建器用：筛出接收部门当前可用的聚合凭据（只含聚合，不含样本明细）。"""
    dept = (recipient_dept or "").strip()
    if not dept:
        return []

    credentials = db.fetchall(
        "SELECT * FROM shared_package_credentials WHERE recipient_dept=? AND status=? ORDER BY id DESC",
        [dept, CREDENTIAL_ACTIVE],
    )
    available: list[dict[str, Any]] = []
    for credential in credentials:
        package = _package(credential["package_id"])
        grant = _grant(credential.get("grant_id"))
        if package is None:
            continue
        state = _authorization_state(package, credential, grant)
        if not state["authorized"]:
            continue
        aggregates = _aggregate_in_scope(package, state)
        available.append({
            "package": serialize_for_recipient(package, state),
            "credential": _serialize_credential(credential),
            "scope": {"regions": state["regions"], "dimensions": state["dimensions"]},
            "aggregates": aggregates,
        })
    return available


# ---------------------------------------------------------------------------
# 读取：打开聚合明细 / 下钻事件明细
# ---------------------------------------------------------------------------

def _aggregate_in_scope(package: dict[str, Any], state: dict[str, Any]) -> list[dict[str, Any]]:
    regions = state["regions"]
    if not regions:
        return []
    placeholders = ",".join("?" for _ in regions)
    rows = db.fetchall(
        f"SELECT region, dimension_key, COUNT(*) AS sample_count, "
        f"COALESCE(SUM(measure), 0) AS measure_sum "
        f"FROM shared_package_samples "
        f"WHERE package_id=? AND data_version=? AND region IN ({placeholders}) "
        f"GROUP BY region, dimension_key ORDER BY region, dimension_key",
        [package["id"], package.get("data_version", 1), *regions],
    )
    return [
        {
            "region": row["region"],
            "dimension_key": row["dimension_key"],
            "sample_count": row["sample_count"],
            "measure_sum": round(float(row["measure_sum"]), 4),
        }
        for row in rows
    ]


def _samples_in_scope(package: dict[str, Any], state: dict[str, Any]) -> list[dict[str, Any]]:
    regions = state["regions"]
    if not regions:
        return []
    placeholders = ",".join("?" for _ in regions)
    return db.fetchall(
        f"SELECT * FROM shared_package_samples "
        f"WHERE package_id=? AND data_version=? AND region IN ({placeholders}) "
        f"ORDER BY id",
        [package["id"], package.get("data_version", 1), *regions],
    )


def serialize_for_owner(package: dict[str, Any]) -> dict[str, Any]:
    data = _serialize_package(package)
    samples = db.fetchall(
        "SELECT * FROM shared_package_samples WHERE package_id=? ORDER BY id", [package["id"]])
    grants = db.fetchall(
        "SELECT * FROM shared_package_grants WHERE package_id=? ORDER BY id", [package["id"]])
    region_counts: dict[str, int] = {}
    for sample in samples:
        region_counts[sample["region"]] = region_counts.get(sample["region"], 0) + 1
    data.update({
        "sample_count": len(samples),
        "region_counts": region_counts,
        "samples": [_serialize_sample(sample) for sample in samples],
        "grants": [_serialize_grant(grant) for grant in grants],
        "read_count": db.count("shared_package_reads", "package_id=?", [package["id"]]),
        "provenance": {
            "owner_dept": package["owner_dept"],
            "source_module": package.get("source_module", ""),
            "source_ref": package.get("source_ref", ""),
            "data_version": package.get("data_version", 1),
        },
    })
    return data


def serialize_for_recipient(package: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """接收方视野：只暴露被授权范围内的计数，不泄露组外样本。"""
    region_counts: dict[str, int] = {}
    for aggregate in _aggregate_in_scope(package, state):
        region_counts[aggregate["region"]] = (
            region_counts.get(aggregate["region"], 0) + aggregate["sample_count"]
        )
    data = _serialize_package(package)
    data.update({
        "sample_count": sum(region_counts.values()),
        "region_counts": region_counts,
        "scope": {"regions": state["regions"], "dimensions": state["dimensions"]},
    })
    return data


def _requested_scope(state: dict[str, Any], requested_regions: list[str] | None) -> dict[str, Any]:
    """请求区域只能收窄、不能越过授权范围。"""
    regions = state["regions"]
    if requested_regions:
        normalized = [region.strip() for region in requested_regions if str(region).strip()]
        narrowed = [region for region in regions if region in normalized]
        if not narrowed:
            return {"ok": False, "reason": "请求区域不在授权范围", "regions": []}
        regions = narrowed
    return {"ok": True, "regions": regions}


def open_package(
    package_id: int,
    *,
    credential_token: str | None = None,
    recipient_dept: str | None = None,
    regions: list[str] | None = None,
    dimensions: list[str] | None = None,
    operation: str = "open",
    hold_ms: int = 0,
) -> tuple[int, dict[str, Any]]:
    """打开共享包或下钻明细。

    返回 (HTTP 状态码, 响应体)。每次调用都会落一条读记录，
    读记录同时保留请求时和返回时的授权状态。
    """
    package = _package(package_id)
    if package is None:
        return 404, {"ok": False, "message": f"共享包 {package_id} 不存在"}

    credential, grant, resolve_error = _resolve_credential(
        package_id,
        credential_token=credential_token,
        recipient_dept=recipient_dept,
    )
    request_state = _authorization_state(package, credential, grant)
    dept = str(
        (credential or {}).get("recipient_dept")
        or recipient_dept
        or ""
    ).strip()

    read_id = _start_read(
        package, request_state,
        credential=credential, grant=grant,
        operation=operation, recipient_dept=dept,
    )

    if not request_state["authorized"]:
        reason = resolve_error or request_state["reason"] or "未授权"
        return 403, _deny(
            read_id, request_state,
            message=f"无权打开共享包：{reason}", package=package,
        )

    # 维度参数只能使用授权维度，否则视为越权请求。
    if dimensions:
        allowed = set(request_state["dimensions"])
        if any(dimension not in allowed for dimension in dimensions):
            return 403, _deny(
                read_id, request_state,
                message="请求维度不在授权范围", package=package,
            )

    scoped = _requested_scope(request_state, regions)
    if not scoped["ok"]:
        return 403, _deny(
            read_id, request_state,
            message=scoped["reason"], package=package,
        )

    request_scope = {
        **request_state,
        "regions": scoped["regions"],
    }

    # 请求时的快照：此刻能取到的、授权范围内的样本（组外样本永远不会进入这里）。
    rows = _samples_in_scope(package, request_scope)
    aggregates = _aggregate_in_scope(package, request_scope)

    # 交错窗口：模拟“返回数据前，拥有方撤销授权”。
    wait_ms = max(0, min(int(hold_ms or 0), MAX_HOLD_MS))
    if wait_ms:
        time.sleep(wait_ms / 1000)

    # 返回前复查：重新读取凭据/授权/包状态，绝不复用请求时的判定结果。
    fresh_package = _package(package_id) or package
    fresh_credential = _credential(credential["id"])
    fresh_grant = _grant(grant["id"])
    return_state = _authorization_state(fresh_package, fresh_credential, fresh_grant)

    if not return_state["authorized"]:
        body = _deny(
            read_id, return_state,
            message="",
            outcome="已撤销未交付", package=package,
        )
        body["auth"]["request"] = {
            "time": body["auth"]["request"]["time"],
            "status": request_state["auth_status"],
            "grant_version": request_state["grant_version"],
            "package_version": request_state["package_version"],
            "scope": scoped["regions"],
        }
        body["auth"]["return"] = {
            "time": body["auth"]["return"]["time"],
            "status": return_state["auth_status"],
            "grant_version": return_state["grant_version"],
            "package_version": return_state["package_version"],
            "scope": return_state["regions"],
        }
        body["message"] = f"返回前授权已失效：{return_state['reason']}，本次未交付"
        body["request_package_version"] = request_state["package_version"]
        body["return_package_version"] = return_state["package_version"]
        return 409, body

    # 返回时如果包版本变化，仍按请求时快照交付，但把两个版本都写进读记录。
    _finish_read(read_id, return_state, outcome="已交付", rows=rows)
    read = db.fetchone("SELECT * FROM shared_package_reads WHERE read_id=?", [read_id])
    payload = {
        "ok": True,
        "read_id": read_id,
        "outcome": "已交付",
        "package_id": package_id,
        "package_name": package["package_name"],
        "package_version": request_state["package_version"],
        "delivered_count": len(rows),
        "auth": {
            "request": {
                "time": read["request_time"],
                "status": request_state["auth_status"],
                "grant_version": request_state["grant_version"],
                "package_version": request_state["package_version"],
                "scope": scoped["regions"],
            },
            "return": {
                "time": read["return_time"],
                "status": return_state["auth_status"],
                "grant_version": return_state["grant_version"],
                "package_version": return_state["package_version"],
                "scope": return_state["regions"],
            },
        },
        "scope": {
            "regions": scoped["regions"],
            "dimensions": request_state["dimensions"],
        },
        "rows": [_serialize_sample(row) for row in rows],
        "aggregates": aggregates,
        "verification": {
            "delivered_hash": read["delivered_hash"],
            "request_package_version": request_state["package_version"],
            "return_package_version": return_state["package_version"],
        },
    }
    if operation == "drill":
        payload["events"] = payload.pop("rows")
        payload["event_count"] = len(payload["events"])
    else:
        payload["details"] = payload.pop("rows")
    return 200, payload


def _read_request_time(read_id: str) -> str | None:
    row = db.fetchone("SELECT request_time FROM shared_package_reads WHERE read_id=?", [read_id])
    return row["request_time"] if row else None


def _read_return_time(read_id: str) -> str | None:
    row = db.fetchone("SELECT return_time FROM shared_package_reads WHERE read_id=?", [read_id])
    return row["return_time"] if row else None


# ---------------------------------------------------------------------------
# 读记录查询与核对
# ---------------------------------------------------------------------------

def get_read(read_id: str) -> dict[str, Any] | None:
    read = db.fetchone("SELECT * FROM shared_package_reads WHERE read_id=?", [read_id])
    if read is None:
        return None
    data = _serialize_read(read)
    rows = data["delivered_rows"]
    recomputed = _sha256(rows) if rows else ""
    package = _package(read["package_id"])
    current_version = package.get("data_version") if package else None
    delivered_hash = data["delivered_hash"] or ""
    data["verification"] = {
        "recomputed_hash": recomputed,
        # 未交付（0 条）时摘要为空，视为一致：核对的是「确实没交付」。
        "hash_matches": recomputed == delivered_hash,
        "request_package_version": data["request_package_version"],
        "return_package_version": data["return_package_version"],
        "current_package_version": current_version,
        "version_matches": current_version is not None
        and current_version == data["request_package_version"],
        "delivered_region_count": len(data["delivered_regions"]),
    }
    data["auth"] = {
        "request": {
            "time": data["request_time"],
            "status": data["request_auth_status"],
            "grant_version": data["request_grant_version"],
            "package_version": data["request_package_version"],
            "scope": data["request_scope"],
        },
        "return": {
            "time": data["return_time"],
            "status": data["return_auth_status"],
            "grant_version": data["return_grant_version"],
            "package_version": data["return_package_version"],
            "scope": data["return_scope"],
        },
    }
    return data


def list_reads(
    package_id: int,
    *,
    owner_dept: str | None,
    limit: int = 50,
) -> tuple[int, dict[str, Any]]:
    package = _package(package_id)
    if package is None:
        return 404, {"ok": False, "message": f"共享包 {package_id} 不存在"}
    if not owner_dept or owner_dept.strip() != package["owner_dept"]:
        return 403, {"ok": False, "message": "只有共享包拥有方可以查看读记录"}

    reads = db.fetchall(
        "SELECT * FROM shared_package_reads WHERE package_id=? ORDER BY id DESC LIMIT ?",
        [package_id, max(1, min(limit, 200))],
    )
    return 200, {
        "ok": True,
        "package_id": package_id,
        "package_name": package["package_name"],
        "provenance": {
            "owner_dept": package["owner_dept"],
            "source_module": package.get("source_module", ""),
            "source_ref": package.get("source_ref", ""),
        },
        "total": db.count("shared_package_reads", "package_id=?", [package_id]),
        "reads": [_serialize_read(read) for read in reads],
    }


def verify_read(read_id: str) -> dict[str, Any] | None:
    """按读记录交付时的版本核对已经查到的正常汇总。"""
    read = get_read(read_id)
    if read is None:
        return None
    return {
        "ok": True,
        "read_id": read_id,
        "outcome": read["outcome"],
        "delivered_count": read["delivered_count"],
        "delivered_regions": read["delivered_regions"],
        "delivered_hash": read["delivered_hash"],
        "verification": read["verification"],
        "auth": {
            "request": {
                "time": read["request_time"],
                "status": read["request_auth_status"],
                "grant_version": read["request_grant_version"],
                "package_version": read["request_package_version"],
                "scope": read["request_scope"],
            },
            "return": {
                "time": read["return_time"],
                "status": read["return_auth_status"],
                "grant_version": read["return_grant_version"],
                "package_version": read["return_package_version"],
                "scope": read["return_scope"],
            },
        },
        "rows": read["delivered_rows"],
    }


# ---------------------------------------------------------------------------
# 演示数据
# ---------------------------------------------------------------------------

DEMO_REGIONS = ("华中", "西北")
DEMO_OWNER = "数据平台部"
DEMO_RECIPIENT = "运营分析部"
DEMO_TOKEN = "spk_demo_huazhong_readonly"


def _demo_samples() -> list[dict[str, Any]]:
    huazhong = [
        ("HZ-001", "浏览商品", "电子产品", 299.0, "2026-11-20T09:05:00Z", "u_hz_001"),
        ("HZ-002", "加入购物车", "电子产品", 299.0, "2026-11-20T09:12:00Z", "u_hz_002"),
        ("HZ-003", "提交订单", "服饰鞋包", 599.0, "2026-11-20T10:01:00Z", "u_hz_003"),
        ("HZ-004", "支付成功", "服饰鞋包", 599.0, "2026-11-20T10:03:00Z", "u_hz_004"),
        ("HZ-005", "评价商品", "生鲜食品", 128.0, "2026-11-20T11:20:00Z", "u_hz_005"),
        ("HZ-006", "分享商品", "电子产品", 299.0, "2026-11-20T12:40:00Z", "u_hz_006"),
    ]
    xibei = [
        ("XB-001", "浏览商品", "电子产品", 199.0, "2026-11-20T08:15:00Z", "u_xb_001"),
        ("XB-002", "加入购物车", "家居日用", 89.0, "2026-11-20T08:45:00Z", "u_xb_002"),
        ("XB-003", "提交订单", "生鲜食品", 45.0, "2026-11-20T13:10:00Z", "u_xb_003"),
        ("XB-004", "支付成功", "家居日用", 89.0, "2026-11-20T13:25:00Z", "u_xb_004"),
    ]
    samples: list[dict[str, Any]] = []
    for region, rows in (("华中", huazhong), ("西北", xibei)):
        for key, event, dimension, measure, occurred_at, user in rows:
            samples.append({
                "sample_key": key,
                "region": region,
                "dimension_key": dimension,
                "event_name": event,
                "measure": measure,
                "occurred_at": occurred_at,
                "attrs": {"region": region, "category": dimension, "user_id": user},
                "data_version": 1,
            })
    return samples


def seed_demo_data() -> dict[str, int]:
    """首次启动时写入演示共享包：华中 6 条 + 西北 4 条，默认只授权华中。"""
    if db.count("shared_packages") > 0:
        return {"shared_packages": 0, "shared_package_samples": 0, "grants": 0}

    package_id = db.insert("shared_packages", {
        "package_name": "华中区域核对只读共享包",
        "description": "供其他部门核对华中区域聚合明细的只读共享包（含华中 6 条、西北 4 条样本）",
        "owner_dept": DEMO_OWNER,
        "source_module": "drill_path",
        "source_ref": "drill_rule:华中区域明细下钻",
        "data_scope": _dumps(list(DEMO_REGIONS)),
        "dimensions": _dumps(["region", "category"]),
        "data_version": 1,
        "status": "已共享",
    })

    samples = _demo_samples()
    for sample in samples:
        db.insert("shared_package_samples", {
            "package_id": package_id,
            "sample_key": sample["sample_key"],
            "region": sample["region"],
            "dimension_key": sample["dimension_key"],
            "event_name": sample["event_name"],
            "measure": sample["measure"],
            "occurred_at": sample["occurred_at"],
            "attrs": _dumps(sample["attrs"]),
            "data_version": sample["data_version"],
        })

    # 关联一条下钻规则，便于从下钻路径进入共享包。
    if not db.fetchone("SELECT id FROM drill_rules WHERE package_id=?", [package_id]):
        db.insert("drill_rules", {
            "rule_name": "华中区域明细下钻",
            "start_metric": "订单总量",
            "drill_dimension": "region",
            "target_module": "event_tracker",
            "default_granularity": "day",
            "status": "已启用",
            "package_id": package_id,
        })

    grant_id = db.insert("shared_package_grants", {
        "package_id": package_id,
        "recipient_dept": DEMO_RECIPIENT,
        "regions": _dumps(["华中"]),
        "dimensions": _dumps(["region", "category"]),
        "grant_version": 1,
        "status": GRANT_ACTIVE,
        "issued_by": DEMO_OWNER,
    })
    db.insert("shared_package_credentials", {
        "token": DEMO_TOKEN,
        "package_id": package_id,
        "grant_id": grant_id,
        "recipient_dept": DEMO_RECIPIENT,
        "regions": _dumps(["华中"]),
        "dimensions": _dumps(["region", "category"]),
        "grant_version": 1,
        "status": CREDENTIAL_ACTIVE,
    })

    return {
        "shared_packages": 1,
        "shared_package_samples": len(samples),
        "grants": 1,
    }
