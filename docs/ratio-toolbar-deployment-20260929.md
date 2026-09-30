# 画面比例与单行工具栏

2026-09-29 按用户截图调整，并发布到原服务器。

## 行为

- 主操作区为单行：任务名称、模型、清晰度、画面比例、时长滑条、声音开关、生成按钮；控件中心在同一水平线上。说明在下方，窄屏仅工具栏横向滚动，不扩大页面宽度。
- 比例选项：adaptive（跟随原片）、16:9、9:16、1:1、4:3、3:4、21:9。保存在草稿模型配置和不可变任务快照中；旧数据缺省 adaptive。
- 官方 Seedance 2.5 编辑 API 仅接受 ratio=adaptive，因此在原始打码缓存之后为任务副本补边，再上传并编辑该副本；完整画面不裁剪，原素材、时长与音轨保留。长边限制 1920，宽高为偶数且严格满足比例。
- Seedance 2.0 系列直接提交所选 ratio。尚未核实比例参数的其他接口只开放 adaptive，不向自定义适配器新增字段。
- 打码预览应用相同补边函数。预览去重参数包含比例；共享打码缓存保持原画幅，避免跨比例污染。
- 草稿切换、刷新、声音设置、时长调整和原有并发配置保持兼容。

## 验证

- 第一轮相关本地回归：96 passed、1 skipped；补充预览缓存测试后第二轮相关回归：77 passed、1 skipped。跳过的是 Windows 无符号链接权限检查。
- 真实 FFmpeg 验证六种比例、准确宽高、完整主体、保留时长/音轨/原文件。
- 浏览器 1440 / 390 / 320 像素验证：所有控件中心高度差小于 3 px、无页面横向溢出、比例保存/刷新/切换模型/提交快照，时长滑条与声音开关回归通过。
- 服务器服务用户隔离测试：115 passed，包括画幅处理、预览缓存、模型参数、任务执行、源片时长、声音和上传传输。未提交付费生成。
- 公网 healthz 和 login 均为 200；三个前端资源字节与发布包一致；20 个发布文件校验通过。

## 发布信息

- 新版本：`/opt/ark-video-workflow/releases/20260929T152844Z-ratio-toolbar`
- 原版本：`/opt/ark-video-workflow/releases/20260929T141642Z-concurrency-duration`
- 备份：`/opt/ark-video-workflow/data/backups/before-ratio-toolbar-20260929T152844Z`
- 标识：`ratio-toolbar-4ece1fbac238`
- 前端资源版本：`20260929-ratio-toolbar-1`
- 确认所有租户任务空闲后发布；历史数据、密码哈希和另一服务 PID 均保持不变。
- 发布脚本：`storage/deploy-ratio-toolbar.py`；基线、发布包和日志：`storage/ratio-toolbar-*`。

参考：[火山方舟创建视频生成任务 API](https://docs.volcengine.com/docs/ark/create-video-generation-task-api?lang=zh&redirect=1)。
