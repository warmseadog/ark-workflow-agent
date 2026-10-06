# 围巾、手饰版本 Git 同步与服务器发布

发布日期：2026-10-06，北京时间约 23:15。用户明确要求提交本地和远程仓库，并同步服务器。

## 版本与同步范围

- 应用提交：`0723d2533f785b88b08298ee0c066ea51ef0c774`。
- 仓库：`https://github.com/warmseadog/ark-workflow-agent`。
- 本地 `main` 由 `a81caad` 快进至该提交；远程 `main` 和 `codex/user-priority-prompt-test` 已原子推送，无强制覆盖。
- 该提交同时补齐已在线上的用户意图优先提示词、清空素材等工作区改动的 Git 记录。发布前按归一化换行符后的文件摘要核对服务器：与本地现存文件的差异仅为围巾、手饰相关的 12 个文件；本次新测试另外加入完整包。
- 发布包 `source_dirty=false`，不包含凭据、真实 `.env` 或数据库。
- 实际上线版本：`0723d2533f785b88b08298ee0c066ea51ef0c774+cd3e2d5cfe810985`。
- 上一版：`a81caadc416371b4bccf40051e1f466f815aaefe+85f2d863e690cdf2`，目录保留。

`docs/scarf-hand-accessories.md` 中“未部署”描述的是此前开发验收时点；`docs/camera-variation.md` 的本地实验状态也属于此前阶段。当前上线状态以本记录为准。后续发布记录提交只更新文档，不要求重新发布相同应用代码。

## 发布方式及验证

使用已跟踪的 `deploy/ecs/release.py` 完整发布流程，先 `check` 再 `deploy` 和 `verify`。发布工具从独立运维候选目录执行，队列空闲检查通过后暂停新请求、停止应用、创建完整快照、原子切换和重启应用，验证后恢复公网请求。

- 本地相关回归：**185 passed**，含桌面/手机新配饰入口、原版/测试版切换、清空素材、时长滑块、素材和提示词链路。
- 服务器隔离测试：**176 passed**，以服务用户运行，独立数据目录、mock 模式、禁用外网；未使用线上数据库或提供商凭据。
- 正式发布工具 `check`、`deploy`、`verify` 全部通过；实际运行目录与版本清单一致。
- HTTPS `/healthz` 和 `/login` 返回 200；带新缓存版本的 `production.js` 返回 200，内容 SHA-256 与本地一致；匿名访问草稿接口返回 401。
- 对三个共享默认模板 `default-exclusive-v2`、`default-yoyo-v3`、`default-0` 执行增量迁移。迁移前正文摘要与备份表逐一核对一致；现正文等于“原正文 + 标记段”，SQLite `quick_check=ok`。
- 未提交付费生成任务；实际成片质量仍需真实素材验收。

全项目测试存在此前已记录的旧 UI 预期与简洁版隐藏样式冲突，未宣称全量套件通过；具体见 `docs/scarf-hand-accessories.md`。

## 服务器记录与恢复位置

- 当前发布目录：`/opt/ark-video-workflow/releases/0723d2533f785b88b08298ee0c066ea51ef0c774+cd3e2d5cfe810985`。
- 独立发布工具：`/opt/ark-video-workflow/operations/scarf-hand-20261006-cd3e2d5cfe81/candidate/deploy/ecs/release.py`。
- 发布前完整快照：`/var/lib/ark-video-workflow-backups/before-release-b844ae658b8145a2bf8ba9e8aa7f7d69`。
- 快照 ID：`cec5bf9293974dc99f65abc1faf8ae53`。
- 本次定点恢复资料：`/opt/ark-video-workflow/operations/scarf-hand-20261006-cd3e2d5cfe81/recovery/`。包含 `rollback_scarf_hand_prompts.py`、开发改动说明、`change.patch`、`manifest.json`、原文件副本及改动前 Git 状态。
- 本机发布日志和测试报告：`exports/scarf-hand-deployment-20261006/`；本机定点恢复包：`exports/scarf-hand-accessories-20261006/`。

默认模板恢复脚本不在完整 release 的源码允许目录 `deploy/ecs/` 下，因此作为独立运维恢复工具存放，未改动发布包白名单。

## 需要回退时

先确认没有运行中任务，并检查是否已有后续发布或管理员编辑。不要直接覆盖数据库，避免丢失部署后产生的数据。

只恢复默认模板正文：先运行恢复脚本预览，确认后加 `--apply`。`--storage-dir` 应为 `/etc/ark-video-release/config.json` 中的 `storage_root`，不能传个人目录。工具仅恢复仍等于此次迁移结果的模板，跳过后续修改和删除项，保留迁移标记。独立入口和动态素材规则仍保留。

```bash
/opt/ark-video-workflow/venv/bin/python /opt/ark-video-workflow/operations/scarf-hand-20261006-cd3e2d5cfe81/recovery/rollback_scarf_hand_prompts.py --storage-dir <实际共享配置目录>
```

完整回到上一发布版：先按上面方式处理默认模板正文，再用正式发布工具检查队列和数据兼容性后回退。下面的 `expected-revision` 仅适用于当前记录的版本；如果已有新发布，必须重新检查，不能机械执行。

```bash
/opt/ark-video-workflow/venv/bin/python /opt/ark-video-workflow/operations/scarf-hand-20261006-cd3e2d5cfe81/candidate/deploy/ecs/release.py rollback --config /etc/ark-video-release/config.json --expected-revision 0723d2533f785b88b08298ee0c066ea51ef0c774+cd3e2d5cfe810985
```

本记录只准备了恢复方法，没有执行回退。新类别的待执行任务应先处理或取消，再切换到不认识该类别的旧代码。
