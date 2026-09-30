# 失败真人入库记录影响新上传的修复

## 行为

新任务重新上传相同图片或人物视频时，历史真人入库记录如果明确失败、从未通过校验，且没有正式素材绑定，不再凭文件指纹认定新上传属于该真人。

- 本地自动虚拟人物归属查询与跨用户共享人物权限查询同时修复。
- 原失败条目和历史任务保留；原素材 ID 仍显示失败，不自动转换人物类型。
- 已成功校验、正式绑定、显式共享来源、处理中和结果不确定的记录继续接受原权限检查。
- 虚拟人入库失败记录继续复用原任务，不通过重新上传绕过失败处理。
- 不迁移或删除数据库、TOS 文件、官方人物组与素材。

## 验证

- 新回归覆盖图片与真实视频文件、同用户重新上传、其他用户上传、共享权限拒绝和素材移除、正式绑定、历史成功校验、未决状态和虚拟人失败。
- 修复前：6 项目标回归失败；修复后通过。审查补充原 ID 拒绝检查，观察到 2 项失败后修正。
- 首轮关联测试：90 项通过。
- 人物库修复时的完整 `pytest tests`：805 项通过、1 项跳过、8 项界面测试失败；存在一个 Starlette/AnyIO 弃用警告。
- 后续八项界面测试已修复，最新完整回归为 813 项通过、0 项失败、1 项跳过。原因及修复见 [界面回归修复记录](ui-regression-repair-20260930.md)。
- 服务器保留线上额外修改的候选版本：71 项关联测试通过，未调用真实生成接口。

完整测试中的界面失败：

1. `test_account_frontend.py::test_admin_users_mutations_filters_stats_and_safe_rendering`
2. `test_account_frontend.py::test_admin_pagination_and_backend_audit_timestamps`
3. `test_account_frontend.py::test_tasks_poll_server_timing_preserving_filter_page_and_user_edits[queued]`
4. `test_account_frontend.py::test_tasks_poll_server_timing_preserving_filter_page_and_user_edits[running]`
5. `test_account_frontend.py::test_generation_controls_have_compact_measured_dimensions[1440-34]`
6. `test_account_frontend.py::test_generation_controls_have_compact_measured_dimensions[390-40]`
7. `test_run_timing.py::test_browser_preserves_historical_name_and_polls_open_timing_details`
8. `test_ui_layout.py::test_all_entrypoints_open_standalone_production`

前 6 项在未修改 Git HEAD 的前端副本中复现，分别涉及分页/筛选预期和旧控件选择器。
后 2 项在保留工作区原有界面改动、将本次两处后端修改恢复为 HEAD 的隔离副本中复现：隐藏菜单点击超时及页面 class 的旧断言。这 8 项均不由本次后端修复引入，未扩展修改其他界面。

## 发布范围

使用 `deploy/ecs/release-failed-real-reupload.py` 捕获线上基线。线上 `portrait_library.py` 包含本地 HEAD 之外的素材移除/可用性修改，因此只替换 `resolve_virtual_assets` 函数；`shared_portraits.py` 在确认整文件基线匹配后应用补丁。发布前核对全部已捕获应用文件哈希，执行隔离回归，确认队列空闲，备份数据库并验证原有数据未变化。旧版本保留用于回滚。

## 已发布

- 北京时间 2026-09-30 11:34，版本 `/opt/ark-video-workflow/releases/20260930T033414Z-failed-real-reupload`。
- 上一版本 `/opt/ark-video-workflow/releases/20260930T025403Z-mobile-landscape` 保留。
- 数据备份 `/opt/ark-video-workflow/data/backups/before-failed-real-reupload-20260930T033414Z`。
- 发布过程再次执行服务器隔离回归：71 项通过；历史数据快照完全一致。
- 发布后健康检查通过，匿名接口仍返回 401，7 个发布文件字节核对一致；其他网站服务进程未改变。
- 用现存 `1000002833.jpg` 失败记录和部署后的真实解析函数做只读检查：独立新上传 ID 不再被归为原真人，原失败素材 ID 仍拒绝自动转虚拟人。未写入业务记录，未调用真实生成接口。
