# 安全加固：HTTPS、上传校验与敏感错误处理

本次修复针对 2026-09-30 检查发现的边界问题，不修改账号、历史任务、媒体原件或平台凭据。

## 实现范围

- `app/secure_transport.py` 为平台接口设置和实际请求提供同一 HTTPS 校验，覆盖已保存配置、环境变量及旧任务快照。凭据请求不跟随跳转；服务商视频引用和结果下载也校验 HTTPS。
- 本机调试必须同时满足 `APP_ALLOW_INSECURE_LOCAL_HTTP=true`、认证关闭、目标是回环地址，才允许 HTTP。该开关默认关闭，生产发布前检查禁止启用。公开视频分享地址无此例外。
- `app/media_validation.py` 在正式制作台、历史上传接口、工作流上传与视频导入入口检查文件扩展名、真实容器/图片格式及可解码内容。拒绝空文件、超限文件、类型伪装和尺寸过大文件；失败时清理临时文件及未入库副本。
- 保留原业务上传字节上限：普通视频按 `MAX_UPLOAD_MB`（默认 512 MiB）、人物视频 50 MiB、图片 20 MiB；工作流接口沿用其已有上限。Nginx 请求体上限为 512 MiB，包含 multipart 边界开销。
- 图片/视频画面单边最大 16384、单帧最大 4000 万像素。多页/动画图片最多 1000 帧，累计解码预算为 1 亿像素。图片能否使用 HEIC/AVIF 等编码仍取决于当前 Pillow 安装的解码器；不能解码的文件返回明确的格式错误。
- `app/security.py` 为校验失败返回固定业务消息，去除错误中的请求输入、平台凭据、Bearer、媒体 URL、Unix/Windows 内部路径。未预期异常返回通用消息与请求编号，日志只记录编号及异常类型。
- 正常 JSON 响应中的错误、消息和日志也通过脱敏，保护历史记录。生产任务读取历史错误时还使用任务私有快照中的旧密钥脱敏；旧任务处理器在错误和失败日志落盘前脱敏，避免密钥轮换后泄露。
- 工作台 8443 与独立媒体 8444 HTTPS 虚拟主机增加 `Strict-Transport-Security: max-age=86400`。媒体路由同时保留既有 CORS 响应头。不启用 preload 或 includeSubDomains；其他站点虚拟主机不变。IP 地址站点的 HSTS 浏览器支持有限，实际安全依赖 TLS 入口、服务仅监听回环地址和 Secure Cookie。

## 验证与发布

新增测试使用合成图片、视频及假密钥，不调用真实付费模型。原有用纯文本模拟媒体的上传夹具改为真正可解码的 PNG/MP4，未通过跳过校验来兼容测试。

发布脚本为 `deploy/ecs/release-security-hardening.py`，复用既有维护、备份、原子切换和失败回滚流程：

1. 比对捕获的线上源码基线，检查生产认证、Secure Cookie、配置 HTTPS、队列和磁盘空间。
2. 复制当前版本到新发布目录，只覆盖已审查应用文件和测试，在临时存储运行服务端回归。
3. 暂停工作台 8443 和媒体 8444 入口，等待旧请求退出；再次确认队列空闲后停服务。其他站点不进入维护状态。
4. 备份数据库、私有配置、环境文件、Nginx 配置，原子切换 `current` 后启动。
5. 验证健康、匿名访问限制、发布文件字节与历史数据库行/列摘要，恢复入口。失败则恢复上一版本及旧代理配置；存在新任务时不强制回滚。
6. 最后单独检查公网证书、TLS、HSTS、登录页和未登录 API 响应。

`check` 和 `probe` 不切换线上服务；`deploy` 执行发布；`verify` 复核当前服务。脚本使用本地 `storage/security-hardening-baseline/baseline.json`，包含发布基线源码与代理配置，不包含私钥或平台密钥。

现有证书采用 Let's Encrypt 短期 IP 证书，续期由 `director-prompt-cert-renew.timer` 每六小时检查，并在成功续期后重载 Nginx。2026-09-30 的定时执行结果正常，`certbot renew --dry-run --cert-name 118.196.7.195 --no-random-sleep-on-renew` 演练通过，未创建重复续期任务。

公开社交媒体来源的无凭据下载仍沿用原有地址校验逻辑；本次强制 HTTPS 的平台请求范围是生成、续写、打码、存储及其生成结果/引用传输。

## 本次上线结果

- 上线时间：2026-09-30 18:35（北京时间）。
- 线上版本：`20260930T103455Z-security-hardening`，应用载荷版本：`security-hardening-af9ceb65246f`，已核对 137 个发布文件。
- 本地最终完整回归：1032 passed、1 skipped、1 warning，耗时 560.09 秒。跳过项为 Windows 缺少符号链接权限；警告为已有 Starlette/AnyIO 弃用提示。
- 发布阶段服务器隔离回归：279 passed、2 skipped、1 warning，耗时 30.06 秒。两项跳过因服务器未安装 Playwright，本地完整回归已覆盖；警告来自 TOS SDK。
- 公网复核：8443、8444 均通过系统 CA 证书校验，协商 TLS 1.3；十项入口检查均符合预期，HSTS 均存在。健康及登录页返回 200，受保护匿名接口返回 401，媒体写入方法返回 405，预检返回 204，工作台媒体重定向保持 HTTPS。媒体 CORS 保持原配置。
- 发布核对确认历史数据库记录及密码哈希未改变，另一站点的服务进程未被重启。未调用真实付费模型。
- 上一版本保留在 `/opt/ark-video-workflow/releases/20260930T091805Z-mira-ui`；发布前数据库与配置备份位于 `/opt/ark-video-workflow/data/backups/before-security-hardening-20260930T103455Z`，备份权限限制为服务端管理员可读。
- 发布清单保存在新版本目录的 `security-hardening-release.json`；本地运行记录位于 `storage/security-hardening-deploy.log`、`storage/security-hardening-verify.log`。
