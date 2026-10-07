# 后台提示词待用版本发布记录

发布日期：2026-10-07。用户明确要求同步远程仓库并上线服务器。

## 版本与入口

- 应用提交：`3a185ae3af6b142d3e6edda26214537b7954051f`。
- 远程仓库：`https://github.com/warmseadog/ark-workflow-agent`，`main` 与 `codex/prompt-standby-admin` 已原子推送，无强制覆盖。发布记录后续仅追加文档提交。
- 实际上线版本：`3a185ae3af6b142d3e6edda26214537b7954051f+403d107d7e315ada`。
- 上一版：`0723d2533f785b88b08298ee0c066ea51ef0c774+cd3e2d5cfe810985`，原目录保留。
- 后台入口：`https://118.196.7.195:8443/admin/settings#prompts`，需管理员或超级管理员登录。
- 功能说明与代码回退方法见 `docs/prompt-standby-admin.md`。

本次只上线机制展示、待用存档及相关后台说明。没有增加启用版本操作，不修改模板正文、默认标识或生成规则。旧的直接编辑共享模板入口保留并折叠，明确说明其生效范围。

## 默认提示词核对

发布前、发布后以只读方式逐条比对三个共享模板的名称、规则版本及正文 SHA-256，全部一致；SQLite `quick_check=ok`。

| 模板 | 规则版本 | 正文 SHA-256 |
| --- | --- | --- |
| yoyo提示词（当前默认） | yoyo-v3 | `7c62b5470e7caebfdc8dcc2e922fe19f0bd664497a0584097e8b7649d5045436` |
| 默认提示词 | exclusive-v2 | `69f59e45ed923676e34ab02dc5a821126f1c09603ca7fefb540a3b7a8fef9db2` |
| 默认提示词2 | legacy-v1 | `ca96fd5b23e168a033d40554fe2e9bdc9193b853673084388e808e213abb972c` |

三份正文均未包含之前撤回的 `【围巾手饰兼容补充 v1】` 标记段。现有围巾、手饰入口及动态素材规则保持原状。本次没有在生产库写入测试待用版本，也没有提交模型生成任务。

## 发布过程与验证

- 本地主工作区相关回归：**95 passed**，覆盖权限、正文隔离、桌面与手机、两种管理员后台及保存失败重试。
- 服务器隔离回归：**78 passed**，应用服务用户、临时数据目录、mock 模式、禁用外网，不继承提供商凭据。
- 完整发布包为干净提交构建（`source_dirty=false`），不包含真实配置、凭据或业务数据库。
- `deploy/ecs/release.py` 的 `check`、`deploy`、`verify` 全部通过。发布前确认队列空闲；短暂维护期间停止新请求、完成完整快照、原子切换并重启，验证后恢复公网。
- 运行进程目录与实际 release 清单一致，服务 `active`，维护标记已解除；旧站点服务保持 `active`，PID 与基线相同。
- 公网 `/healthz`、`/login` 和三份后台脚本均返回 200，脚本字节与发布来源一致。
- 匿名访问 `/api/admin/prompt-standby` 和 `/api/production/drafts` 均返回 401。

## 恢复位置

- 独立发布工具：`/opt/ark-video-workflow/operations/prompt-standby-20261007-403d107d7e31/candidate/deploy/ecs/release.py`。
- 发布前完整快照：`/var/lib/ark-video-workflow-backups/before-release-2a48fc0ddfeb45a4b04c8eb7dea7853f`。
- 快照 ID：`bb0464771a524961ba421cb12673325f`。
- 快照清单 SHA-256：`801d4cec82497b5696ee50dfa4c21d8d1660377dd938a8737e900851dd44e80d`。
- 本机发布日志及核对结果：`exports/prompt-standby-deployment-20261007/`（不纳入 Git）。
- 待用版本首次保存后位于全局存储根下 `private/prompt-standby.db`，处于完整备份的数据根覆盖范围内。

如需撤回本次服务器发布，先确认当前没有后续发布并等待队列空闲，再使用既有工具：

```sh
/opt/ark-video-workflow/venv/bin/python /opt/ark-video-workflow/operations/prompt-standby-20261007-403d107d7e31/candidate/deploy/ecs/release.py rollback --config /etc/ark-video-release/config.json --expected-revision 3a185ae3af6b142d3e6edda26214537b7954051f+403d107d7e315ada
```

该流程切回前一代码版本并保留业务数据，不覆盖数据库。待用库可以保留，旧代码不会读取；不应恢复旧 `local-preferences.db`，以免覆盖发布后的合法修改。默认提示词此前的正文恢复操作与本次功能独立。
