# 耳环配饰上线记录

- 用户明确授权上传四个应用文件到 118.196.7.195，并允许备份、发布和重启方舟服务。
- 发布时间：2026-09-29 14:55:49（北京时间）。
- 发布目录：`/opt/ark-video-workflow/releases/20260929T065549Z-earrings`。
- 前一版本：`/opt/ark-video-workflow/releases/20260929T060451Z-material-pagination`，已保留。
- 备份：`/opt/ark-video-workflow/data/backups/before-earrings-20260929T065549Z`。
- 更新文件：`app/reference_roles.py`、`app/production_store.py`、`app/static/production.js`、`app/templates/production_panel.html`。
- 线上版本比本地 HEAD 更新。发布前获取并核对线上文件，在其基础上精确应用耳环差异，保留缩略图、素材分页和其他既有线上更新。未将本地旧文件直接覆盖线上。发布合并文件保存在本机 `storage/earrings-release-files/`；当前本地 app 仍为原本地基线加耳环改动，后续发布必须继续核对线上版本。
- 发布前所有用户处理队列空闲；暂停入口、停止服务后再次核对并备份 9 个数据库及配置，再切换新版本。历史数据库记录摘要保持一致。
- 服务账号隔离验证通过：登录、耳环入口渲染、耳环上传、保存及草稿恢复、参考图角色映射。未提交付费生成任务。
- 上线后及独立复验：四文件字节一致，健康接口正常，匿名 API 拒绝访问，公网登录 HTTP 200；服务 active/running，NRestarts=0。其他站点服务 PID 保持不变。
- 本机发布日志：`storage/earrings-deploy-check.log`、`storage/earrings-deploy-deploy.log`、`storage/earrings-deploy-verify.log`。
