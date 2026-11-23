"""业务路由汇总：资源路由 + 管道路由。"""
from __future__ import annotations

from app.routers import resource
from app.routers import pipeline
from app.routers import shared_package
from app.routers import sharing

ROUTERS = [resource]
PIPELINE_ROUTER = pipeline.router
# 独立前缀的共享包路由与资源路由互不冲突，单独注册。
EXTRA_ROUTERS = [shared_package.router, sharing.router]