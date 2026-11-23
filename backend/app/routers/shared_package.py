"""只读共享包接口：登记、授权、凭据、打开/下钻与读记录核对。

约定：
  - 拥有方操作（签发/撤销/查读记录）用 owner_dept/actor_dept 参数
    或 X-Actor-Dept 请求头标明部门（中文部门名建议走参数）；
  - 接收方读取用 credential_token 或 recipient_dept 表明身份；
  - 任何一次打开/下钻都会生成 read_id，可用 /reads/{read_id} 追查
    请求时与返回时的授权状态和交付内容。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.database import db
from app.services import shared_package

router = APIRouter(prefix="/api/shared-packages", tags=["只读共享包"])


class SamplePayload(BaseModel):
    sample_key: str | None = None
    region: str
    dimension_key: str = ""
    event_name: str = ""
    measure: float = 0.0
    occurred_at: str | None = None
    attrs: dict[str, Any] = Field(default_factory=dict)


class PackagePayload(BaseModel):
    package_name: str
    owner_dept: str
    description: str = ""
    source_module: str = "drill_path"
    source_ref: str = ""
    data_scope: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    data_version: int = 1
    samples: list[SamplePayload] = Field(default_factory=list)


class GrantPayload(BaseModel):
    recipient_dept: str
    regions: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    grant_version: int = 1
    credential_token: str | None = None


class OpenPayload(BaseModel):
    credential_token: str | None = None
    recipient_dept: str | None = None
    regions: list[str] | None = None
    dimensions: list[str] | None = None
    hold_ms: int = 0


def _actor(
    actor_dept: str | None,
    owner_dept: str | None,
    query_actor: str | None = None,
) -> str | None:
    """部门身份优先取查询参数（中文部门名走请求头会乱码），其次请求头。"""
    return (query_actor or owner_dept or actor_dept or "").strip() or None


@router.get("")
def list_packages(
    actor_dept: str | None = Query(default=None),
    owner_dept: str | None = Query(default=None),
    actor_header: str | None = Header(default=None, alias="X-Actor-Dept"),
) -> dict[str, Any]:
    """拥有方视角：列出本部门登记的全部只读共享包。"""
    dept = _actor(actor_header, owner_dept, actor_dept)
    if not dept:
        raise HTTPException(status_code=400, detail="缺少 X-Actor-Dept")
    rows = db.fetchall(
        "SELECT * FROM shared_packages WHERE owner_dept=? ORDER BY id DESC", [dept])
    return {
        "items": [shared_package.serialize_for_owner(row) for row in rows],
        "total": len(rows),
        "owner_dept": dept,
        "demo_recipient": shared_package.DEMO_RECIPIENT,
    }


@router.post("")
def create_package(payload: PackagePayload) -> dict[str, Any]:
    return shared_package.create_package(payload.model_dump())


@router.get("/available")
def available_packages(
    recipient_dept: str = Query(..., description="接收部门"),
) -> dict[str, Any]:
    """接收方视角：只看得到被分享范围内的包和聚合数据。"""
    items = shared_package.available_for(recipient_dept)
    return {
        "items": items,
        "total": len(items),
        "recipient_dept": recipient_dept,
    }


@router.get("/reads/{read_id}")
def get_read(read_id: str) -> dict[str, Any]:
    """按读记录追查：请求时/返回时的授权状态、交付样本与版本核对。"""
    data = shared_package.get_read(read_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"读记录 {read_id} 不存在")
    return data


@router.get("/{package_id}")
def get_package(
    package_id: int,
    actor_dept: str | None = Query(default=None),
    owner_dept: str | None = Query(default=None),
    recipient_dept: str | None = Query(default=None),
    credential_token: str | None = Header(default=None, alias="X-Package-Credential"),
    actor_header: str | None = Header(default=None, alias="X-Actor-Dept"),
) -> dict[str, Any]:
    """拥有方看到全量；接收方只看到授权范围内的包信息。"""
    package = db.fetchone("SELECT * FROM shared_packages WHERE id=?", [package_id])
    if package is None:
        raise HTTPException(status_code=404, detail=f"共享包 {package_id} 不存在")

    owner = _actor(actor_header, owner_dept, actor_dept) or ""
    if owner and owner == package["owner_dept"]:
        return shared_package.serialize_for_owner(package)

    credential, grant, error = shared_package._resolve_credential(
        package_id,
        credential_token=credential_token,
        recipient_dept=recipient_dept,
    )
    state = shared_package._authorization_state(package, credential, grant)
    if not state["authorized"]:
        raise HTTPException(status_code=403, detail=f"无权打开共享包：{error or state['reason']}")
    return shared_package.serialize_for_recipient(package, state)


@router.post("/{package_id}/open")
def open_package(package_id: int, payload: OpenPayload | None = None) -> dict[str, Any]:
    """接收方打开共享包，读取授权区域/维度的聚合明细。"""
    payload = payload or OpenPayload()
    status, body = shared_package.open_package(
        package_id,
        credential_token=payload.credential_token,
        recipient_dept=payload.recipient_dept,
        regions=payload.regions,
        dimensions=payload.dimensions,
        operation="open",
        hold_ms=payload.hold_ms,
    )
    return _respond(status, body)


@router.post("/{package_id}/drill")
def drill_package(package_id: int, payload: OpenPayload | None = None) -> dict[str, Any]:
    """接收方下钻共享包，返回同样权限筛选后的事件明细。"""
    payload = payload or OpenPayload()
    status, body = shared_package.open_package(
        package_id,
        credential_token=payload.credential_token,
        recipient_dept=payload.recipient_dept,
        regions=payload.regions,
        dimensions=payload.dimensions,
        operation="drill",
        hold_ms=payload.hold_ms,
    )
    return _respond(status, body)


@router.post("/{package_id}/grants")
def issue_grant(
    package_id: int,
    payload: GrantPayload,
    actor_dept: str | None = Query(default=None),
    owner_dept: str | None = Query(default=None),
    actor_header: str | None = Header(default=None, alias="X-Actor-Dept"),
) -> dict[str, Any]:
    result = shared_package.issue_grant(
        package_id, payload.model_dump(), owner_dept=_actor(actor_header, owner_dept, actor_dept))
    if result.get("status") == 403:
        raise HTTPException(status_code=403, detail=result["message"])
    return result


@router.post("/{package_id}/grants/{grant_id}/revoke")
def revoke_grant(
    package_id: int,
    grant_id: int,
    actor_dept: str | None = Query(default=None),
    owner_dept: str | None = Query(default=None),
    actor_header: str | None = Header(default=None, alias="X-Actor-Dept"),
) -> dict[str, Any]:
    result = shared_package.revoke_grant(
        package_id, grant_id, owner_dept=_actor(actor_header, owner_dept, actor_dept))
    if result.get("status") == 403:
        raise HTTPException(status_code=403, detail=result["message"])
    return result


@router.get("/{package_id}/credentials")
def list_credentials(
    package_id: int,
    actor_dept: str | None = Query(default=None),
    owner_dept: str | None = Query(default=None),
    recipient_dept: str | None = Query(default=None),
    include_revoked: bool = Query(default=False),
    actor_header: str | None = Header(default=None, alias="X-Actor-Dept"),
) -> dict[str, Any]:
    package = db.fetchone("SELECT * FROM shared_packages WHERE id=?", [package_id])
    if package is None:
        raise HTTPException(status_code=404, detail=f"共享包 {package_id} 不存在")
    owner = _actor(actor_header, owner_dept, actor_dept)
    is_owner = bool(owner) and owner == package["owner_dept"]
    if not is_owner and not recipient_dept:
        raise HTTPException(status_code=403, detail="缺少部门身份")
    items = shared_package.list_credentials(
        package_id,
        recipient_dept=None if is_owner else recipient_dept,
        include_revoked=include_revoked,
        reveal_token=is_owner,
    )
    return {"items": items, "total": len(items), "package_id": package_id}


@router.get("/{package_id}/reads")
def list_reads(
    package_id: int,
    actor_dept: str | None = Query(default=None),
    owner_dept: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    actor_header: str | None = Header(default=None, alias="X-Actor-Dept"),
) -> dict[str, Any]:
    """拥有方追查看过这个包的读记录（含来源与当时版本）。"""
    status, body = shared_package.list_reads(
        package_id, owner_dept=_actor(actor_header, owner_dept, actor_dept), limit=limit)
    return _respond(status, body)


def _respond(status: int, body: dict[str, Any]) -> dict[str, Any]:
    if status >= 400:
        raise HTTPException(status_code=status, detail=body)
    return body
