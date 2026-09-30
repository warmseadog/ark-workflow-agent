# 2026-09-29 模型提交上传超时修复

已部署版本：`/opt/ark-video-workflow/releases/20260929T085336Z-upload-timeout`

上一版本：`/opt/ark-video-workflow/releases/20260929T073840Z-task-models`

回滚备份：`/opt/ark-video-workflow/data/backups/before-upload-timeout-20260929T085336Z`

## 根因与修复

两条任务 `6aa2bf9f30514050b5862563133abd36`、`1c81b02216d5449f923284b6819c4cd4` 在提交阶段出现连接错误。衣服图和处理后的发型图经 Base64 编码约 5.68 MB。服务器以原配置并发发送同等大小的合成请求时，复现了 15.02 秒的 `ConnectionError -> ProtocolError -> TimeoutError(The write operation timed out)`；将诊断超时放宽后两条均正常收到响应。原任务未保存底层异常，以上是同环境对照复现证据。

- POST 网络请求超时从 `(15, 120)` 改为 `(60, 120)`。
- 同一服务进程的 POST 请求串行发送；GET 轮询及结果下载不持有上传锁。
- 区分连接超时、嵌套写入超时、等待响应超时。
- 错误日志仅记录方法、阶段、耗时、超时值和异常类型，不记录凭证、地址、提示词或素材。
- 不自动重发提交结果不确定的请求。

线上使用一个 uvicorn 进程、多条 worker 线程，进程内锁适用于当前架构。未来增加服务进程时需重新设计跨进程上传限流。

## 验证

- 新增测试先验证原实现：5 失败、1 通过；修复后 6 通过。
- provider/worker 相关首轮测试：30 通过。
- 独立审查及 provider/recovery/transport 测试：39 通过，无需修改的问题。
- 服务器以服务用户执行新测试：6 通过。
- 健康检查、登录页面、匿名 API 拒绝访问、发布文件一致性检查通过。
- 部署前后历史数据库记录一致，密码哈希未变化。
- 已部署代码真实并发大请求：两条都到达官方接口，耗时 8.824 秒、18.303 秒（包含上传锁等待），均未发生传输异常。
- 实测使用缺少 model/content 的合成诊断请求，预期响应 HTTP 400 MissingParameter，不创建视频任务，不发送用户素材。

完整正式测试集结果：705 通过、1 跳过、5 失败。失败如下：

1. `tests/test_account_frontend.py::test_admin_users_mutations_filters_stats_and_safe_rendering`
2. `tests/test_account_frontend.py::test_admin_pagination_and_backend_audit_timestamps`
3. `tests/test_account_frontend.py::test_tasks_poll_server_timing_preserving_filter_page_and_user_edits[queued]`
4. `tests/test_account_frontend.py::test_tasks_poll_server_timing_preserving_filter_page_and_user_edits[running]`
5. `tests/test_virtual_library.py::test_virtual_upload_and_generation_require_active_official_aigc`

前四项对应已有后台前端的分页大小 50→10、筛选立即提交，与旧测试预期不一致；测试不调用本次修改的 provider。第五项单独重跑通过（1 passed），完整测试中的偶发原因未证实，其路径也不调用修改的传输代码。

初次在仓库根目录直接运行 pytest 时，还扫描到了 storage 下旧项目副本和独立模块，导致 61 项重复模块收集错误；随后限定正式 `tests` 目录运行，结果如上。

首次发布预检因服务用户无法在 release 目录创建默认测试 storage 而中止，尚未停流或切换版本。改用独立临时测试目录后发布成功。

原两条任务记录保留，没有自动创建重复生成任务。本次验证覆盖请求上传与接口响应，不代表已重新生成原任务的视频。

## 本地记录

- `storage/upload-timeout-deploy.log`
- `storage/upload-timeout-verify.log`
- `storage/upload-timeout-live-verification.log`
- `storage/upload-timeout-suite.log`
- `storage/deploy-upload-timeout.py`
- `storage/verify-upload-timeout-remote.py`
