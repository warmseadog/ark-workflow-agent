# 左侧导航与视频库发布

- 公网入口：https://118.196.7.195:8443/ ，视频库 `/videos`，任务记录 `/#tasks`。
- 发布时间：2026-09-29 18:06:54（北京时间）。
- 当前目录：`/opt/ark-video-workflow/releases/20260929T100654Z-workspace-library`。
- 前版目录：`/opt/ark-video-workflow/releases/20260929T093549Z-audio-controls`。
- 数据库和配置备份：`/opt/ark-video-workflow/data/backups/before-workspace-library-20260929T100654Z`。
- 发布标识：`workspace-library-c520e278347a`；21 个发布文件（18 个应用文件、3 个测试文件）。

发布前发现一条正在生成的任务，等待队列自然清空后才进行维护与版本切换。使用原有发布保护流程：线上文件指纹校验、独立临时数据测试、短暂维护入口、停服后二次队列检查、SQLite 一致性备份、原子切换和失败自动回滚。仅重启方舟服务，其他站点服务进程保持不变。

发布包以当前线上版本为底稿，保留线上独有的人物管理路由和请求计时中间件，以及密码页的已有静态资源版本。没有用本地较旧的 main.py 整体覆盖远程入口。

验证结果：

- Linux 上以服务账号运行首帧、视频库分页、历史兼容、任务列表和播放测试：13 passed。
- 发布后 21 个文件指纹匹配，实际 HTTP 静态响应字节匹配，healthz 200，匿名私有 API 401。
- 公网 HTTPS 的 5 个前端文件与发布包一致；匿名 `/videos` 跳转登录，视频库 API 拒绝匿名访问。
- 使用三份生产任务库的一致性副本核对视频库数量：14、0、0；从已有成片成功提取 JPEG 首帧。渲染制作页、视频库、人物页、后台及密码页均只有一个共享侧栏。
- 历史数据库记录与密码哈希保持不变。验证没有提交生成请求，没有修改生产素材、草稿、任务或账号。

本地发布文件保存在 `storage/workspace-release-package/`；发布驱动为 `storage/deploy-workspace-layout.py`；部署日志为 `storage/workspace-library-deploy.log`；只读核验脚本为 `storage/verify-workspace-live.py`，日志为 `storage/workspace-library-live-verification.log`。独立核验进程需使用运行服务的 `LD_LIBRARY_PATH`。

恢复时先确认任务队列空闲，再切换 current 到前版本并重启 `ark-video-workflow.service`。本次不含数据库模式迁移。
