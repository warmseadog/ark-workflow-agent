# 打码覆盖范围修复发布记录

发布日期：2026-10-07。用户明确要求先发布服务器，再同步远程仓库。

## 发布内容

- 火山 MediaKit 的 `face_box_expand` 改为 `mask_scale - 1`，与本地 deface 每边扩展的语义一致。1.4 对应 40%，1.8 对应 80%；超过 2.0 时保留原参数转本地处理，不截小覆盖范围。
- 更新本地与外部打码缓存版本，新预览和新任务不复用旧缓存。历史成片及已有任务工作文件保留。
- 后台移除“遮挡目标”，新建草稿默认固定为人脸遮挡。旧全局头发默认值读取为人脸，历史草稿、复制草稿和任务快照保留原参数。

## 版本

- 应用提交：`30d5251e7d7cd3767f5319f35882e6a410e6a36f`。
- 实际上线版本：`30d5251e7d7cd3767f5319f35882e6a410e6a36f+9378beb08de92232`。
- 上一版本：`3a185ae3af6b142d3e6edda26214537b7954051f+403d107d7e315ada`。
- 发布包 SHA-256：`f5103aa2ee0cda13eec57a9c6a47848dfd28ad4c2f04ea788e76dcae7ce7ac37`。
- 发布包来源为干净提交，`source_dirty=false`，没有额外未跟踪文件。
- 后台入口：`https://118.196.7.195:8443/admin/settings#redaction`，需要管理员登录。
- 远程仓库：`https://github.com/warmseadog/ark-workflow-agent`，发布后向 `main` 普通快进推送；本文件为后续文档提交。

## 验证与维护过程

- 本地相关接口与浏览器回归：145 passed，2 skipped，1 deselected。排除的 Windows 子进程后代清理测试此前已确认在修改前代码中同样失败；两项跳过受 Windows 环境限制。
- 服务器 Linux 隔离回归：146 passed，包含 Windows 上排除的子进程清理案例。以应用用户运行，使用临时数据根、mock 模式、禁用 dotenv 和外网，不继承服务商凭据。
- 正式预检最初因活动任务拒绝。等待期间持续有新任务，因此临时只暂停本站的新生成请求，保留查询、素材访问和已有任务运行。队列空闲后恢复原 nginx 配置，再立即交由正式发布流程维护切换。
- `deploy/ecs/release.py` 的 check、deploy、verify 均通过，发布回执阶段为 `verified`。完整快照后原子切换，实际进程目录与 release 一致，服务 active，维护标记解除。
- 服务环境文件、外部打码连接配置、默认打码配置均以发布前后哈希核对，无变化；未更换 Key 或修改后台默认数值。其他站点服务 PID 保持不变。
- 公网 `/healthz`、`/login` 返回 200；匿名配置和草稿接口返回 401；新后台脚本返回 200，SHA-256 与发布包一致：`7a6ba38a410c4c86cb4745234da36a758fff9d51c78826814d3b144b7316d0a8`。
- 本次没有创建付费打码或视频生成测试任务。扩展比例已对齐，云端与本地检测框及遮罩形状仍可能不同。

## 回退资料

- 运维目录：`/opt/ark-video-workflow/operations/mask-coverage-20261007`，包含独立发布工具候选副本、基线、检查、回归、发布及验证日志。
- 发布前快照：`/var/lib/ark-video-workflow-backups/before-release-69d315f53af94e5e9063f6f844ac9b54`。
- 快照 ID：`b6ac0040605141318fd37271f60ca68f`。
- 快照清单 SHA-256：`7c322d44cee4188532eeb479cc70204cbdbe1aa1ae7e5d0bb67ca4255ac3c5dc`。
- 旧 release 与历史快照保留，未删除业务视频或数据库。

如需回退，应先确认没有后续发布并等待队列空闲，使用正式工具，不恢复旧数据库：

```sh
/opt/ark-video-workflow/venv/bin/python /opt/ark-video-workflow/operations/mask-coverage-20261007/candidate/deploy/ecs/release.py rollback --config /etc/ark-video-release/config.json --expected-revision 30d5251e7d7cd3767f5319f35882e6a410e6a36f+9378beb08de92232
```
