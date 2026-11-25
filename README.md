# 分拣过机台（Sortbay）

骨架：过机扫描装载 → 口门路由裁决 → 错分回流批次 → 封袋班次对账

- 前端 http://127.0.0.1:6600
- API http://127.0.0.1:11600

## 单袋封袋口径

本仓库全局只采用一种架构：**袋体写边界全关**（`bag_body_write_boundary_closed`）。

封袋确认事件成功后：

- 拒绝再入袋；
- 拒绝把该袋/袋内件做错分回流；
- 拒绝把袋内件改路由，也拒绝会改写该目的地封袋件路由的路由表变更；
- 袋内件数冻结为封口事件中的 `sealed_count`；
- 封袋页、扫描册、口门在口、总览、审计等读投影保持开放，并可由同一个 `seal_event_id` 重放。

不采用“禁止物理再塞、逻辑纠偏仍开”的混合口径；界面和接口不会同时宣称回流/改路由仍开放。架构 A 也不提供可改写状态的解封旁路；`POST /api/封袋/{id}/解封` 会显式返回 409，避免猜测出的管理入口破坏同一写边界。

## 关键接口

- `POST /api/封袋/{bag_id}/封袋确认`（兼容原 `POST /api/封袋/{bag_id}/封口`）
- `POST /api/封袋/封袋确认` / `POST /api/封袋确认`：请求体可用 `袋编号` 或 `bag_id`
- `POST /api/封袋/{bag_id}/入袋`
- `POST /api/回流批次`
- `POST /api/扫描/{scan_id}/改路由`（也支持 `POST /api/扫描/改路由`）
- `POST /api/路由表`：若目的地已有封袋件且口门发生变化，拒绝脏写
- `GET /api/封袋/封口事件/{event_id}/重放`
- `GET /api/封袋/{bag_id}/封口事件`（同义 `/api/封袋/{bag_id}/重放`）
- `GET /api/审计/封口事件/{event_id}/重放`（同义 `GET /api/审计/重放?event_id=...`）

封口、事件、袋态、件数更新和审计在同一个 `BEGIN IMMEDIATE` 写事务中提交；任一步失败整体回滚，避免封口/入袋半截导致袋态和件数拧花。

## 本地验证

```bash
# 后端
python3 -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements-dev.txt
PYTHONPATH=backend pytest -q backend/tests

# 前端
npm install --prefix frontend
npm run build --prefix frontend
```

端到端夹具覆盖：封后仍入袋、封后仍回流、封后改路由脏写、未封误挡、封口/入袋半截回滚，以及并发封口与入袋。
