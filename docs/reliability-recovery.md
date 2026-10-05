# 失败重试与数据恢复操作手册

本次覆盖检测项 6「失败与重复操作可控」和 7「数据能够恢复」。资源配额策略不在本次变更范围内。代码默认不会执行备份、开启定时器或联系云存储；部署模板须由运维配置并启用。

## 任务失败与继续检查

- 旧工作流使用数据库原子领取和执行令牌。同一个项目、阶段及幂等键复用原任务；相同键携带不同输入会拒绝。后台调度器持有数据库对应的进程锁，启动时恢复可安全继续的任务。
- 生成请求提交前持久化阶段；收到远端任务 ID 后先持久化，再轮询/下载。远端已接收的任务只查询原 ID。提交结果不明时暂停，不能用自动重试重新 POST。
- 旧工作流的「继续检查」只适用于已经有远端任务 ID 的记录。没有 ID 的结果不明任务须在服务商控制台核对；不能推断「查不到就没有提交」。
- 照片每个查询窗口最多 60 次逻辑查询或 1800 秒，先到即停止。连续查询错误按 5、15、30、60 秒退避；重启不重置次数、截止时间或退避。逻辑查询中 SDK 的有限网络重试仍遵循原传输配置。
- 照片停止后可手动继续一个新窗口。有远端 ID 就查询该 ID，没有 ID 就查找原创建记录；不会再次创建官方素材。失败发生在提交前的任务仍可重新排队。
- 部署继续使用单个 Uvicorn worker（现有 ECS 模板 `--workers 1`）。本次没有把整套人物素材创建流程改造成多进程执行器。

## 恢复目标及备份内容

初始运维目标建议为 **RPO 24 小时、RTO 4 小时**。这是待部署演练确认的目标，不是本地测试已证明的生产承诺。每日 03:00 Asia/Shanghai 备份，每 15 分钟检查一次备份健康。24 小时从快照创建时间计算；上传耗时也计入。每日调度如持续上传较慢，可提前调度以避免超过目标。

快照包含整个持久存储根：主数据库、各用户数据库、账户/私有配置、原始素材、输出视频、仍需使用的工作文件、人物状态，以及显式配置的工作流数据库和文件根。SQLite 使用原生备份 API 并执行完整性检查，不直接拷贝仍有 WAL 的数据库文件。清单记录文件大小、SHA-256、目录、权限模式和代码版本。

缓存、可重建的缩略图/预览、旧备份目录、临时锁和未完成分片不会进入快照。已登记媒体引用缺文件、外部数据库或文件根未纳入、源目录重叠、symlink/junction/reparse、校验不一致都使备份失败。当前后端只支持文件型 SQLite；PostgreSQL 等连接串会明确拒绝，需要另行实现对应的一致备份工具。

快照期间必须停止所有写入者。ECS 包装器停止指定应用 systemd 服务，检查停止成功后创建快照，并在成功/失败后恢复原先运行的服务；原本停止的服务保持停止。压缩、加密和异地上传在应用重启后进行。独立运行的脚本、另一个应用实例或独立 worker 也必须纳入停写安排；一个 systemd 单元不能停止它不管理的进程。

异地归档使用 AES-256-GCM 流式认证加密。密钥独立保存，不能处于任何快照源或备份输出目录。上传后完整读回归档核对 SHA-256 和大小，再上传并读回成功凭据。只有这两步都成功才更新 `last-success.json`。任一步失败（包括配置和密钥预检失败），最新尝试记录为失败，健康检查失败；不会借用上次成功伪装本次成功。

## 首次部署

先安装更新后的依赖。Linux 锁定依赖已包含 `cryptography`。以下路径与仓库 ECS 部署模板一致；若服务路径不同，需同步配置、单元文件的 `WorkingDirectory` / `ExecStart` / `ReadWritePaths`。

1. 创建 `/etc/ark-video-backup` 和 `/var/lib/ark-video-workflow-backups`，属主 root，权限 0700。
2. 复制 `deploy/ecs/backup.config.example.json` 为 `/etc/ark-video-backup/config.json`；配置实际存储根、独立备份目录、应用 env 文件、工作流数据库/文件根及需要恢复的其他配置。`release_root` 指向正在运行的发布目录（ECS 为 `/opt/ark-video-workflow/current`），自动读取并核对 `release-manifest.json` 和 `REVISION`，快照同时保存实际内容版本及基础提交；文件缺失、损坏或标记冲突会在停服前拒绝。只有尚未迁移到新发布清单的旧部署，才可不设 `release_root` 并显式填写完整 `code_revision` SHA；这只记录旧提交，不能证明未提交内容。若两个字段同时设置，SHA 必须与清单一致。额外根必须存在且不能彼此重叠。反向代理/TLS 等配置若需要恢复，也应逐项加入 `extra_roots`。
3. `WORKFLOW_DB`、`WORKFLOW_STORAGE` 必须在应用 env 或备份配置中明确为绝对路径，与服务实际运行设置一致。不要从 `STORAGE_DIR` 猜测；应用未配置时的默认工作流路径受工作目录影响。应用 env 会被纳入加密备份；不会通过 dotenv 隐式加载其他文件。
4. 为备份创建专用私有 bucket / prefix 和独立凭据，配置 `backup.env.example` 为 `/etc/ark-video-backup/credentials.env`。授予该 prefix 所需上传、读取、列举权限；只有需要执行过期清理的运维凭据才授予删除权限。不要将其作为公开媒体 bucket 使用。
5. 生成单独的 32 字节随机密钥；密钥及凭据文件权限 0600，并在独立的安全位置保存可恢复副本。丢失密钥无法恢复归档；不要在同一个 bucket 明文存放密钥。示例（首次创建，拒绝覆盖已有文件）：

   ```sh
   /opt/ark-video-workflow/venv/bin/python -c "import os,base64; p='/etc/ark-video-backup/encryption.key'; f=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600); os.write(f,base64.urlsafe_b64encode(os.urandom(32))); os.close(f)"
   ```

6. 在 `notify_command` 配置现有告警程序的 argv 数组。程序通过 stdin 接收 JSON，不经过 shell，30 秒超时。模板为空数组时只写状态文件和 systemd 失败状态，**没有外部通知**；启用前应接入告警并验证送达。配置错误、上传错误和过期均应告警。
7. 将四个 `ark-video-backup*.service/.timer` 模板安装到 systemd，先执行 `systemd-analyze verify` 和一次人工备份、异地下载恢复演练，通过后再启用两个 timer。此仓库变更没有执行这些部署动作。

备份单元的 `ReadWritePaths` 必须包含备份目录、实际存储根及所有独立 SQLite 源目录：SQLite 的只读连接在 WAL 模式下也可能需要创建 WAL/SHM 侧文件。包装器先停写，快照引擎只用 SQLite 只读连接读取源库。[systemd 官方服务文档](https://github.com/systemd/systemd/blob/main/man/systemd.service.xml)说明 `ExecStopPost` 也用于启动失败后的清理；定时器中的时区写法遵循[官方时间表达式文档](https://github.com/systemd/systemd/blob/main/man/systemd.time.xml)。仍应在目标 Linux 上验证模板。

手动运行等同于 service 的命令时，须显式向进程注入专用 `ARK_BACKUP_ACCESS_KEY` / `ARK_BACKUP_SECRET_KEY`；不要将凭据写到命令行历史。

```sh
cd /opt/ark-video-workflow/current
/opt/ark-video-workflow/venv/bin/python -m app.backup_operations --config /etc/ark-video-backup/config.json backup
/opt/ark-video-workflow/venv/bin/python -m app.backup_operations --config /etc/ark-video-backup/config.json check
```

`last-attempt.json`、`last-success.json` 和 `receipts/` 在备份目录中。`check` 不健康时退出码为 1。备份进程异常结束时，systemd 的 `ExecStopPost` 根据持久化的 `service-recovery.json` 恢复原先运行的应用；机器断电仍需要依靠启动流程/运维恢复。出现遗留 `.backup-operation.lock` 时，不会擅自删除：先确认备份进程全部退出，执行 `recover-service`，确认应用状态，再由管理员移除该锁。不要在备份还运行时执行此恢复命令。

发布、备份和 `recover-service` 共用备份目录下的 `.maintenance-operation.lock` 操作系统锁，避免互相重启服务。进程退出或崩溃会释放锁；该文件保留在磁盘上，**不要仅因文件存在就删除它**。如果 `.release-maintenance.json` 仍存在，备份及其恢复钩子会拒绝启动应用；先按 [发布与回退](release-safety.md) 的恢复步骤核对并完成上次发布。

## 保留策略

已校验的远端备份按北京时间保留最近 7 个有备份的日期、4 个 ISO 周、3 个自然月的代表快照，合并去重；缺失日期不会伪造快照。清理默认只列出候选，不删除。`--apply` 显式执行删除，逐个完整读回校验后先删除成功凭据，再删除归档，避免留下指向不存在归档的成功标记。

```sh
python -m app.backup_operations --config /etc/ark-video-backup/config.json prune
# 审核候选后按运维流程执行：
python -m app.backup_operations --config /etc/ark-video-backup/config.json prune --apply
```

本地明文快照仅依靠目录权限保护，当前不自动删除，便于失败排查。应在异地校验且恢复演练完成后，按实际磁盘容量安排本地保留/清理；不要把备份根放到静态资源或应用公开下载路径。远端孤立归档（未生成成功凭据）不纳入自动清理。

## 异地恢复和演练

恢复必须使用与归档匹配的密钥。先准备可用代码/依赖及独立的新目录，停止目标应用和所有写入者。以下恢复目标都必须不存在；不得直接覆盖原生产数据。

```sh
python -m app.backup_operations --config /etc/ark-video-backup/config.json list
python -m app.backup_operations --config /etc/ark-video-backup/config.json retrieve SNAPSHOT_ID /srv/ark-restore/snapshot
python -m app.backup verify /srv/ark-restore/snapshot
python -m app.backup restore /srv/ark-restore/snapshot /srv/ark-restore/data \
  --extra-destination application_env=/srv/ark-restore/config/app.env \
  --extra-destination service_unit=/srv/ark-restore/config/ark-video-workflow.service
python -m app.recovery_guard status /srv/ark-restore/data
```

`--extra-destination` 必须完整对应 manifest 中的 named roots；示例仅对应默认配置。恢复会再次校验文件和数据库，把已知媒体路径映射到新根，保留远端任务 ID。任意 prompt 中碰巧出现的路径文本不会被改写。恢复保留 POSIX 权限模式，不复制 UID/GID、Windows ACL；激活前以服务用户恢复，或将恢复文件属主显式调整为实际服务用户。Windows 演练不代替 Linux 的权限及 systemd 验证。

恢复时自动写入 `.restore-hold.json`。应用启动看到标记后不启动后台处理，并对非静态接口返回 503。未完成的生成、打码、人物照片、人物准备、播放准备和待确认会话进入 `restore_held`，原记录写入对应数据库的 `restore_quarantine`，防止旧快照的 queued/submitting 任务再次提交。

核对步骤：

1. 记录演练开始时间、快照时间/ID、清单校验结果、各 SQLite `PRAGMA integrity_check` 结果、恢复文件数及总量。
2. 抽查原始素材、完成视频、账户和配置，验证数据库引用可定位到恢复文件；不要让演练环境使用真实提供商密钥发新任务。
3. 核对每个数据库的 `restore_quarantine` 原状态和远端 ID，与服务商控制台对账。已接收的任务只查询原任务；没有 ID 的提交不明记录保持隔离，不能直接设 queued。历史隔离记录目前没有批量自动放行命令，必须逐项人工核对和处置。
4. 将新部署的存储、数据库、工作流路径改为恢复根；env 和 systemd 文件中的路径由管理员显式修改，不能原样启用旧配置。
5. 完成核对且应用仍停止时，仅解除全局门禁，再启动服务：

   ```sh
   python -m app.recovery_guard release-hold /srv/ark-restore/data \
     --snapshot-id SNAPSHOT_ID --operator OPERATOR \
     --note "已核验数据库、媒体、配置及远端任务；历史不明任务保持隔离"
   ```

   解除动作在 `private/recovery-releases/` 留审计记录。它不会把历史任务重新排队；正常的新业务才可恢复处理。

6. 记录服务恢复时间，计算实际 RPO（故障时刻减快照时刻）和 RTO（故障/演练开始至业务可用），记录失败项和负责人。首次启用、重要模式变更后和定期运维检查都应实际做一次新目录恢复。

## 本地验证边界

新增测试使用临时数据库/文件、模拟对象存储和断点，覆盖加密往返、错误密钥/篡改、远端回读损坏、备份失败后服务恢复、完整异地链路模拟、恢复隔离、重复请求和查询窗口。真实云账号、异地存储权限、告警送达、Linux systemd 停启、Linux 原子目录发布和生产数据规模下的 RPO/RTO，必须在部署演练中验证。

## 本次本地验收记录（2026-10-05）

基线：本地 `main`，提交 `09b6f2f5c6f549b0d99f3377254e7716df581a9d`。实现留在工作区；原先 10 个已修改/未跟踪文件逐个 SHA-256 核对，内容未被覆盖。未部署或启用真实定时任务。

| 验证 | 实际结果 |
| --- | --- |
| 旧工作流可靠性及相关回归 | 98 passed；其中独立浏览器复核 9 passed |
| 照片窗口、旧照片库和人物准备 | 最后后端批次 41 passed；另有离线浏览器检查通过 |
| 最后备份、传输、运维和恢复保护批次 | 53 passed，1 skipped（Windows 无符号链接创建权限） |
| 全量 `tests/` 首轮 | 1120 passed，29 failed，2 skipped，700.64 秒 |
| 首轮失败项的隔离重跑 | 28 个路径/产物环境失败全部通过；相关三批共 33、6、2 项通过 |
| 仍失败的既有断言 | `test_account_frontend.py::test_generation_controls_fit_desktop_and_mobile[1440]` |

全量首轮的 27 个浏览器失败均为截图硬编码写入仓库 `storage` 时的 `PermissionError`；另一个 discovery 测试主动将配置覆盖为相对 `storage`，绕过测试存储隔离。复验只在临时测试入口重定向截图及该测试存储路径，没有改变产品断言或放宽权限。测试截图仍实际生成。

唯一保留的布局断言要求桌面全部控件高 32–40px，而基线 MIRA 样式已将时长滑块设为 44px。使用原始 HEAD 模板、静态资源及测试对照，当前版本与 HEAD 均在 1440px 失败、390px 通过；该测试不执行本次修改的业务脚本。本次未修改无关的页面布局来使它通过，因此不能声称完整测试集全绿。

全量两项 skip 均涉及 Windows 符号链接权限；测试还有一项既有 Starlette/AnyIO 弃用警告。完整本地日志、独立审查记录和隔离运行入口保留在仓库忽略的 `.tmp-reliability-20261005/` 中，重点为 `full-suite.log`、`backup-all-final.log`、`legacy-report.md`、`portrait-report.md`、`layout-diagnosis.md` 和 `isolated-ui-rerun*.log`。这些临时文件用于本次验收，不是运行应用或部署备份的依赖。
