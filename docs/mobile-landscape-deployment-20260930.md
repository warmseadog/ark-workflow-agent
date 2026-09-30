# 制作页横竖屏优化发布

2026-09-30 10:54（北京时间）已部署到现有方舟服务。

- 公网入口：https://118.196.7.195:8443/
- 当前版本：`/opt/ark-video-workflow/releases/20260930T025403Z-mobile-landscape`
- 上个版本：`/opt/ark-video-workflow/releases/20260930T021338Z-batch-photos-mobile`
- 备份：`/opt/ark-video-workflow/data/backups/before-mobile-landscape-20260930T025403Z`
- 发布标识：`mobile-landscape-5cfd0b24fe6a`
- 静态资源版本：`20260930-mobile-landscape-1`

## 使用变化

手机竖屏使用单列素材卡片和固定底部生成栏。参考图、场景、发型、配饰分清必填与可选，提示当前缺少的素材；场景关闭后保留输入，修改草稿后清除上一版提交反馈，未确认提交仍保留恢复状态。

宽度不超过 1100px 的横屏使用收起式导航、56px 顶栏和紧凑单行底部操作栏。素材在较窄横屏分两列，740px 起分三列；生成模型、清晰度与画面比例同排，时长和声音分区。可选设置展开后占整行，旋转不重建表单或清空输入。左右留白支持设备安全区。

## 验证

- 捕获线上源代码作为发布基线，确认目标应用文件与本地 HEAD 基线一致。发布副本以线上代码为底稿，仅覆盖 9 个前端文件，不发布本地其他后端差异。
- 发布副本第一轮 74 项相关回归通过，覆盖素材、任务、声音、视频与人物库以及横竖屏。
- 补充平板横屏后，最终发布副本 21 项浏览器检查通过，包含手机竖屏、667×375、740×360、844×390、932×430、1024×768、桌面及横竖屏旋转保留输入。
- 服务器使用服务账号、独立临时数据进行模板渲染和 API 检查，35 项通过；未调用付费视频生成。
- JavaScript 语法检查与 `git diff --check` 通过。
- 公网 TLS 校验通过；`/healthz`、`/login` 返回 200，匿名私有 API 返回 401。
- 四个公网 CSS/JS 响应与发布文件逐字节一致；9 个服务器应用文件全部校验一致。
- 服务 `active/running`，`NRestarts=0`。切换前后历史数据库记录和账号密码哈希一致，其他网站服务 PID 未变。

上述为针对性回归及模拟屏幕尺寸验证，并非物理手机键盘/刘海的实机验收或整套项目测试全绿声明。此前全量测试的 8 项既有失败已在修改前代码复现，本次没有修改相关后端逻辑。

## 发布及回退

发布前后检查各处理队列为空；隔离检查通过后临时暂停当前站点请求、完成 SQLite 一致性备份、原子切换 current、健康及历史数据检查，随后恢复请求。没有变更数据库结构。

如需回退，应先等待队列空闲并暂停新请求，再将 current 指向上个版本并重启 `ark-video-workflow.service`；保留当前数据，不用旧数据库覆盖发布后产生的任务。

- 发布驱动：`deploy/ecs/release-mobile-layout.py`
- 源码基线：`storage/mobile-layout-live-baseline.json`
- 发布副本：`storage/mobile-layout-release-package/`
- 部署、只读验证日志：`storage/mobile-layout-deploy.log`、`storage/mobile-layout-verify.log`
- 公网校验：`storage/mobile-layout-public-verification.json`

发布包不包含凭据或用户素材。源码修改保留在当前工作区，未提交 Git 或推送远端。
