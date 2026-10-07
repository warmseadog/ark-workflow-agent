# Seedance 输入校验与自动适配发布记录

发布日期：2026-10-07。用户明确授权部署服务器并同步 GitHub。

## 发布内容

- 参考图片按 EXIF 方向归一化；比例超出 0.4–2.5 时使用中性背景补边，保留完整图像和原始文件，提交适配副本。
- Seedance 动作视频按模型能力校验实际输入时长。前端在超限或时长未知时禁止普通生成和换拍法；后端入队前、工作任务执行及提交前校验，不能通过缩短输出时长绕过。
- 打码视频像素不足 407696 时等比放大至偶数尺寸，例如 576×576 → 640×640；缓存适配结果，检查可解码性、时长及帧率，提交任务目录中的适配副本，保留原视频和原打码文件。
- 换拍法规划对 5 毫秒以内的舍入误差做边界对齐，严格检查规范化结果；格式、时间及固定条件错误分别提示。原始规划及规范化结果记录在私有诊断文件中。
- 任务详情展示适配前后尺寸；本地素材适配失败明确归为提交前失败，避免标成提交结果不确定。
- 将线上已有的外部打码并发修复同步纳入 Git 历史。与发布前线上清单核对，相应实现字节一致，没有回退该修复。

## 版本与仓库

- 代码提交：`e2c9bddd3e81f8bf8ea4317cb0bac72055a28d7b`。
- 实际上线版本：`e2c9bddd3e81f8bf8ea4317cb0bac72055a28d7b+98fcbaf490dcd77e`。
- 上一版本：`86861cc8a8e470fbed7194f7150f30542f6ae1fd+9279d055b1bcf931`。
- 发布包 SHA-256：`df6e73b15f4b723af6aae3eaa4f790205890c1d16ec5f3e887b05a908e941b5d`。
- 包来源为干净提交：`source_dirty=false`，无额外未跟踪文件。正式包逐文件哈希与隔离测试的候选包一致。
- 仓库：<https://github.com/warmseadog/ark-workflow-agent>，向 `main` 普通快进推送；本记录为后续文档提交。

## 验证与发布过程

- 本地修复及桌面/手机浏览器回归：24 passed。
- 本地发布工具及模板回退回归：37 passed，1 skipped（Windows 上的 POSIX 权限用例）。
- 服务器 Linux 隔离回归：260 passed，1 deselected。使用服务账号、临时数据根、禁用 dotenv、mock 配置及隔离网络命名空间，不继承生产凭据。覆盖四项修复、外部打码并发、超时后子进程清理、任务恢复和相关版本兼容性。
- 服务器未安装开发用 Git；依赖 Git 的发布构建测试在本地执行。非运行包内的旧模板回退脚本测试亦在本地执行。未为测试安装或修改服务器运行依赖。
- 正式预检检测到 1 条生成中任务并拒绝切换。等待其自然完成后才发布，没有取消任务或强制重置任务状态。
- 正式发布工具完成队列复核、维护、完整快照、原子切换、实际进程健康检查及解除维护；发布回执 `phase=verified`。
- 独立核验实际进程目录、完整代码清单和运行依赖。应用 MainPID `375904`，NRestarts `0`；另一站点 PID 保持 `674`。
- 公网 `/healthz`、`/login` 返回 200；匿名草稿及模型设置接口返回 401；两个新版制作页脚本返回 200，响应 SHA-256 与清单一致。
- 应用环境文件与 nginx 配置的发布前后哈希一致，维护标记已解除。
- 本次未发起真实规划、打码或视频生成测试。真实模型最终接收及生成结果以用户后续任务为准。

## 回退与审计

- 运维目录：`/opt/ark-video-workflow/operations/input-repairs-20261007`，保留候选包、正式包、隔离回归日志、配置哈希基线、发布回执及独立核验记录。
- 发布前快照：`/var/lib/ark-video-workflow-backups/before-release-6c93e942cd7d4b11a522edfd905afbe5`。
- 快照 ID：`53bdd585e716466b904fddb0c60dd9b5`。
- 快照清单 SHA-256：`fabe28a09e8736bdee2ab56c866072e6bfaed53642c2687d8140c5a694949307`。
- 旧 release、备份与业务数据保留。本次没有执行备份清理。

如需回退，先确认没有后续发布并等待任务队列空闲，再使用正式工具；不要覆盖业务数据库：

```sh
/opt/ark-video-workflow/venv/bin/python /opt/ark-video-workflow/operations/input-repairs-20261007/candidate/deploy/ecs/release.py rollback --config /etc/ark-video-release/config.json --expected-revision e2c9bddd3e81f8bf8ea4317cb0bac72055a28d7b+98fcbaf490dcd77e
```
