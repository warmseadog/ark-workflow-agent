# 女装素材发现工作台实施计划

**Goal:** 基于已确认架构交付本机可运行的案例驱动发现、真实链接导入、审核和 v1 打码/生成工作台。

**Architecture:** 保留 FastAPI 和现有 deface 两阶段处理；新增独立 discovery 路由、来源适配器、SQLAlchemy 存储和参考 HTML 风格的 studio 页面。PostgreSQL 通过 DATABASE_URL 配置，本机默认 SQLite 文件持久化。生产任务仍为本机线程，不宣称 Temporal、向量检索和视频视觉分析已上线。

**Tech Stack:** FastAPI、SQLAlchemy 2、PostgreSQL/SQLite、原生 HTML/CSS/JS、现有 yt-dlp/deface/SeedanceClient。

**Spec:** 本任务用户已批准的上一轮架构方案及当前 v1 集成要求。

## 约束

- 附件 index.html 仅为视觉参考，不执行其中脚本或当作指令。
- 初始数据库为空，无伪造博主、链接、热度、匹配概率或进度。
- 无数据接口时返回平台搜索入口，不冒充已完成平台抓取。
- 基于公司案例生成查询计划；小样本使用结构化标签匹配，不强制 RAG。
- 保留 /api/jobs 两阶段接口，原页面迁到 /v1，新页面在 /。
- 审核与启动打码是两个动作；未通过不能启动；候选启动使用 face 模式。
- 每个媒体任务持久化状态，重启后的执行中任务标记中断，不假装自动恢复。
- API 密钥只在服务端环境变量中，页面只显示连接状态。

## Tasks

- [ ] 1. 添加行为测试：平台 URL 验证/去重、批量部分失败、案例查询计划、审核门禁、任务关联和重启恢复。先运行失败证据。
- [ ] 2. app/database.py、app/discovery/{schemas,service,sources,routes}.py：存储、排序、导入、审核、查询计划和可配置 HTTP 来源。要求 source 状态明确、错误可见、未知指标为 null。
- [ ] 3. app/jobs.py、app/main.py：持久化现有任务，/v1 页面、候选关联校验、重复启动返回同一个任务；保留打码后预览门禁。tests 验证源文件上传 fallback 和 face 模式。
- [ ] 4. app/templates/studio.html、app/static/studio.{css,js}：参考页面米白/炭黑/砖红风格，搜索计划、平台筛选、候选详情审核、案例 CRUD、任务队列和内嵌制作台。
- [ ] 5. app/static/app.js、v1-theme.css：真实候选加载/恢复任务、输入校验、错误可见、安全渲染日志，原打码功能回归。
- [ ] 6. 更新 README、.env.example、依赖与 PostgreSQL compose；删掉本轮产生的临时假数据页面。
- [ ] 7. 运行完整 pytest、JS 语法检查、API 冒烟；Playwright 验证桌面/移动、导入-审核-制作、案例计划和截图。用本地短片实测 deface；没有真实模型密钥则只验证现有明确标注的 mock。

## 验证契约

POST /api/discovery/import 接收 links/title/tags；逐项返回 imported/duplicates/errors，重复导入不修改人工审核结果。
POST /api/discovery/runs 接收 topic/platforms/case_ids，返回查询计划和候选数以及来源状态；空案例可直接生成主题搜索。
POST /api/discovery/candidates/{id}/review 接收 decision/note；制作中的候选禁止改审。
POST /api/jobs 增加 candidate_id；审核通过才可创建，重复点击不重复执行，失败任务允许明确重试。
GET /api/jobs 返回持久化队列；/v1?job=... 能恢复预览与后续生成。
POST /api/discovery/cases 保存真实案例标签、镜头特点、成功/失败原因；DELETE 支持删除错误录入。
