# 实时数据分析与可视化应用平台

支撑实时数据聚合、多维度分析、可视化图表生成和数据应用发布的分析平台

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/views/SharedPackageView.vue  共享包核对（授权/读取/审计）
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/routers/shared_package.py  只读共享包接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/shared_package.py  共享包授权与读取审计
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8019/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5199/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8019`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 实时聚合器 | `realtime_agg` | 聚合窗口 | 窗口名称、数据源、聚合维度 |
| 事件追踪器 | `event_tracker` | 事件定义 | 事件名称、事件属性、触发条件 |
| 仪表盘构建器 | `dashboard_builder` | 仪表盘 | 仪表盘名称、包含图表、刷新间隔 |
| 查询缓存层 | `query_cache` | 缓存配置 | 配置名称、关联数据集、缓存时长秒数 |
| 分群引擎 | `segment_engine` | 分群定义 | 分群名称、分群条件、所属人口特征 |
| 指标存储 | `metric_store` | 指标快照 | 指标名称、快照值、快照时间 |
| 下钻路径 | `drill_path` | 下钻规则 | 规则名称、起始指标、下钻维度 |
| 分析告警策略 | `alert_policy` | 告警规则 | 规则名称、监控指标、告警条件 |
| 数据报告 | `data_report` | 报告生成器 | 报告名称、包含指标集、报告周期 |
| 异常归因 | `anomaly_analysis` | 归因分析器 | 分析对象、异常时间点、归因维度 |
| 预测模型 | `forecast_model` | 预测任务 | 任务名称、预测指标、预测周期 |
| 分群对比 | `cohort_compare` | 对比报告 | 对比名称、分群A、分群B |
| 分析结果导出 | `data_export` | 导出任务 | 导出名称、查询条件、导出格式 |
| 数据订阅 | `data_subscription` | 订阅任务 | 订阅名称、关联指标、推送条件 |
| 查询历史 | `query_history` | 查询记录 | 查询时间、执行人、涉及数据集 |
| 报告快照 | `report_snapshot` | 快照记录 | 快照名称、所属报告、快照时间 |
| 数据API | `data_api` | 数据接口 | 接口名称、关联指标、请求方式 |
| 留存分析 | `retention_analysis` | 留存任务 | 任务名称、行为事件、留存天数 |
| 漏斗分析器 | `funnel_analyzer` | 漏斗定义 | 漏斗名称、步骤序列、转化窗口小时 |
| 多维数据集构建 | `cube_builder` | Cube定义 | Cube名称、事实表、维度组合 |

## 只读共享包（跨部门核对）

下钻路径原先只能读取本部门的全量明细，其他部门没有可核对的只读口径。现在
新增「只读共享包」，把指定区域/维度的聚合明细按授权分享给接收部门：

- 拥有方登记共享包（含华中 6 条、西北 4 条样本），只向接收部门授权华中；
- 接收部门必须领取授权，授权签发只读凭据（聚合凭据）；
- 打开/下钻只返回「包范围 ∩ 授权范围 ∩ 请求范围」内的样本，
  组外样本（如西北）任何时刻都不会进入返回或核对记录；
- 没有领取授权的部门既打不开包，也查不到包内样本；
- 每次读取都写一条读记录 `shared_package_reads`，分别记录请求时和返回时的
  授权状态、授权版本与包版本；
- 返回前会重新复查授权：如果在返回数据前权限被撤销，本次不交付（`409`），
  读记录标记为「已撤销未交付」；撤销后发起的新读取立即失效；
- 拥有方仍可按读记录、交付摘要和当时版本追查这个包的来源与已交付汇总。

仪表盘构建器在接收方视角先用共享包筛出可用聚合凭据，再把凭据用于下钻；
下钻返回的事件明细与打开明细走同一套权限筛选。

### 关键接口

```text
GET  /api/shared-packages?actor_dept=数据平台部            拥有方共享包与样本分布
GET  /api/shared-packages/available?recipient_dept=运营分析部 接收方可见范围与聚合
GET  /api/shared-packages/{id}                             拥有方全量 / 接收方受限视图
POST /api/shared-packages/{id}/open                        打开聚合明细（留读记录）
POST /api/shared-packages/{id}/drill                       下钻事件明细（留读记录）
POST /api/shared-packages/{id}/grants?owner_dept=…         签发只读授权与凭据
POST /api/shared-packages/{id}/grants/{grant_id}/revoke?owner_dept=…  撤销授权
GET  /api/shared-packages/{id}/reads?owner_dept=…          拥有方追查读记录
GET  /api/shared-packages/reads/{read_id}                  按读记录核对请求/返回时授权
POST /api/drill-path/{rule_id}/execute                     执行绑定共享包的下钻规则
GET  /api/dashboard-builder/credentials?recipient_dept=…   仪表盘筛选可用聚合凭据
GET  /api/dashboard-builder/{id}/data?recipient_dept=…     仪表盘按凭据下钻取数
```

`open`/`drill` 支持 `hold_ms`（≤15000）把返回延后，便于验证「返回前撤销」的交错场景。
前端新增「共享包核对」页（`/shared-packages`），可切换部门分别查看拥有方授权、
接收方读取和读记录核对。回归测试：`cd backend && .venv/bin/python -m pytest tests -q`。

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：数据工程
子方向：数据分析与数据应用
