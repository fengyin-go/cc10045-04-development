"""只读共享包回归测试：授权范围、组外样本、撤销、交错读取与读记录核对。

运行：cd backend && .venv/bin/python -m pytest tests -q
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time

import pytest

# 测试用独立数据库目录，必须在导入 app 之前设置。
_TMP = tempfile.mkdtemp(prefix="sp_test_")
os.environ["CC10045_DB_DIR"] = _TMP

from app.database import db  # noqa: E402
from app.services import shared_package  # noqa: E402

OWNER = shared_package.DEMO_OWNER
RECIPIENT = shared_package.DEMO_RECIPIENT
TOKEN = shared_package.DEMO_TOKEN


@pytest.fixture(scope="module", autouse=True)
def demo():
    shared_package.seed_demo_data()
    package = db.fetchone("SELECT * FROM shared_packages LIMIT 1")
    assert package is not None
    yield package


@pytest.fixture()
def fresh_package():
    """每个测试建一个独立的包和授权，避免互相干扰。"""
    package_id = db.insert("shared_packages", {
        "package_name": "测试共享包",
        "description": "",
        "owner_dept": OWNER,
        "source_module": "drill_path",
        "source_ref": "",
        "data_scope": json.dumps(["华中", "西北"]),
        "dimensions": json.dumps(["region", "category"]),
        "data_version": 1,
        "status": "已共享",
    })
    for key, region in [
        ("T-HZ-1", "华中"), ("T-HZ-2", "华中"), ("T-HZ-3", "华中"),
        ("T-XB-1", "西北"), ("T-XB-2", "西北"),
    ]:
        db.insert("shared_package_samples", {
            "package_id": package_id,
            "sample_key": key,
            "region": region,
            "dimension_key": "electronics",
            "event_name": "浏览商品",
            "measure": 100.0,
            "occurred_at": "2026-11-20T00:00:00Z",
            "attrs": "{}",
            "data_version": 1,
        })
    issued = shared_package.issue_grant(
        package_id,
        {"recipient_dept": RECIPIENT, "regions": ["华中"]},
        owner_dept=OWNER,
    )
    assert issued["ok"], issued
    return {
        "package_id": package_id,
        "grant_id": issued["grant"]["id"],
        "token": issued["credential"]["token"],
    }


def test_demo_package_has_six_huazhong_four_xibei(demo):
    samples = db.fetchall(
        "SELECT region, COUNT(*) AS cnt FROM shared_package_samples "
        "WHERE package_id=? GROUP BY region", [demo["id"]])
    counts = {row["region"]: row["cnt"] for row in samples}
    assert counts.get("华中") == 6
    assert counts.get("西北") == 4


def test_recipient_only_sees_authorized_region(fresh_package):
    items = shared_package.available_for(RECIPIENT)
    target = next(item for item in items if item["package"]["id"] == fresh_package["package_id"])
    assert target["package"]["region_counts"] == {"华中": 3}
    assert target["package"]["sample_count"] == 3
    assert target["scope"]["regions"] == ["华中"]


def test_open_delivers_only_authorized_samples(fresh_package):
    status, body = shared_package.open_package(
        fresh_package["package_id"], credential_token=fresh_package["token"])
    assert status == 200
    assert body["delivered_count"] == 3
    assert {row["region"] for row in body["details"]} == {"华中"}
    assert not any(row["region"] == "西北" for row in body["details"])


def test_unregistered_department_cannot_open_or_see(demo):
    status, body = shared_package.open_package(demo["id"], recipient_dept="财务部")
    assert status == 403
    assert body["delivered_count"] == 0
    assert shared_package.available_for("财务部") == []


def test_request_cannot_widen_scope(fresh_package):
    status, body = shared_package.open_package(
        fresh_package["package_id"],
        credential_token=fresh_package["token"],
        regions=["西北"],
    )
    assert status == 403
    assert body["delivered_count"] == 0


def test_revoke_invalidates_new_reads(fresh_package):
    revoked = shared_package.revoke_grant(
        fresh_package["package_id"], fresh_package["grant_id"], owner_dept=OWNER)
    assert revoked["ok"]

    status, body = shared_package.open_package(
        fresh_package["package_id"], credential_token=fresh_package["token"])
    assert status == 403
    assert body["delivered_count"] == 0
    assert body["auth"]["request"]["status"] == "未授权"


def test_interleaved_read_records_request_and_return_states(fresh_package):
    """接收方打开后、返回前拥有方撤销：要么交付华中且可留痕，要么不交付。"""
    captured: dict[str, object] = {}

    def reader() -> None:
        captured["result"] = shared_package.open_package(
            fresh_package["package_id"],
            credential_token=fresh_package["token"],
            hold_ms=1200,
        )

    thread = threading.Thread(target=reader)
    thread.start()
    time.sleep(0.3)
    shared_package.revoke_grant(
        fresh_package["package_id"], fresh_package["grant_id"], owner_dept=OWNER)
    thread.join(timeout=10)
    assert not thread.is_alive()

    status, body = captured["result"]
    assert status == 409
    assert body["delivered_count"] == 0
    assert body["outcome"] == "已撤销未交付"
    assert body["auth"]["request"]["status"] == "已授权"
    assert body["auth"]["return"]["status"] == "未授权"

    record = shared_package.get_read(body["read_id"])
    assert record is not None
    assert record["request_auth_status"] == "已授权"
    assert record["return_auth_status"] == "未授权"
    assert record["delivered_count"] == 0
    assert record["delivered_regions"] == []


def test_interleaved_read_before_revoke_is_traceable(fresh_package):
    """没有交错时交付华中，并能按读记录用当时版本核对。"""
    status, body = shared_package.open_package(
        fresh_package["package_id"], credential_token=fresh_package["token"])
    assert status == 200
    record = shared_package.get_read(body["read_id"])
    assert record["outcome"] == "已交付"
    assert record["delivered_regions"] == ["华中"]
    assert record["delivered_count"] == 3
    assert record["verification"]["hash_matches"] is True
    assert record["verification"]["version_matches"] is True


def test_owner_can_still_trace_package_after_revoke(fresh_package):
    status, body = shared_package.open_package(
        fresh_package["package_id"], credential_token=fresh_package["token"])
    assert status == 200
    shared_package.revoke_grant(
        fresh_package["package_id"], fresh_package["grant_id"], owner_dept=OWNER)

    status, listing = shared_package.list_reads(
        fresh_package["package_id"], owner_dept=OWNER)
    assert status == 200
    assert listing["provenance"]["owner_dept"] == OWNER
    assert any(read["read_id"] == body["read_id"] for read in listing["reads"])


def test_non_owner_cannot_read_audit(fresh_package):
    status, _ = shared_package.list_reads(fresh_package["package_id"], owner_dept="运营分析部")
    assert status == 403


def test_grant_regions_must_be_inside_package(fresh_package):
    result = shared_package.issue_grant(
        fresh_package["package_id"],
        {"recipient_dept": "市场部", "regions": ["华南"]},
        owner_dept=OWNER,
    )
    assert result["ok"] is False


def test_package_version_change_keeps_request_snapshot(fresh_package):
    status, body = shared_package.open_package(
        fresh_package["package_id"], credential_token=fresh_package["token"])
    assert status == 200
    db.update("shared_packages", {"data_version": 2}, "id=?", [fresh_package["package_id"]])

    record = shared_package.get_read(body["read_id"])
    assert record["request_package_version"] == 1
    assert record["verification"]["current_package_version"] == 2
    assert record["verification"]["version_matches"] is False
    # 已交付内容仍可按当时版本核对。
    assert record["verification"]["hash_matches"] is True


def test_reissue_revokes_previous_credential(fresh_package):
    old_token = fresh_package["token"]
    again = shared_package.issue_grant(
        fresh_package["package_id"],
        {"recipient_dept": RECIPIENT, "regions": ["华中"], "grant_version": 2},
        owner_dept=OWNER,
    )
    assert again["ok"]
    status, _ = shared_package.open_package(
        fresh_package["package_id"], credential_token=old_token)
    assert status in (401, 403)
    status, body = shared_package.open_package(
        fresh_package["package_id"], credential_token=again["credential"]["token"])
    assert status == 200
    # 同一部门只保留一条有效凭据。
    active = shared_package.list_credentials(fresh_package["package_id"], recipient_dept=RECIPIENT)
    assert len(active) == 1
