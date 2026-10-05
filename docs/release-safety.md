# ECS 发布、版本身份与回退

本次提供本地代码及离线测试，没有执行发布、SSH、恢复、安装依赖或启用定时器。当前 ECS 的 Linux systemd/nginx、原子目录切换和权限仍须在维护窗口演练。

## 入口与完整包

使用受版本管理的 `deploy/ecs/release.py`。`storage/release-mira-oct05.py` 仅转发该入口，不再隐式执行 SSH。历史增量发布脚本的远端 `deploy/probe` 已拒绝执行，避免绕过完整包、依赖和降级检查；历史 `check/verify` 仅用于旧流程核验。所有模式使用 argparse 枚举；未知模式返回 2。

构建命令在源代码目录执行：

```text
python deploy/ecs/release.py build --output /reviewed-output/release.tar.gz
```

构建包含 `app/`、`tests/`、`ui/`、`deploy/ecs/`、根 `requirements.txt`，以及明确列出的 7 个已跟踪本地打码脚本、模型和依赖声明。不会扫描或打包其他 `storage/` 内容。发现上述源码目录有未跟踪文件时停止，先逐项审阅，再对每一个明确加入 `--include-untracked relative/path`。不可批准数据库、私钥、真实 `.env` 或符号链接；打码运行资源必须已经受 Git 跟踪。已删除文件不会从旧版本继承回来。

包中的 `release-manifest.json` 记录实际文件 SHA-256、大小、权限、基础提交、工作区是否修改、批准的未跟踪文件、Linux 精确依赖及数据读取能力。`revision` 为 `source_commit+content_sha256前16位`；相同输入字节产生相同压缩包。`REVISION` 与清单一致。主应用的登录后 `/api/version` 和备份读取同一身份；清单、版本标记或依赖清单不一致会拒绝。

构建将根需求及打码处理器需求与 Linux lock 逐项比对；缺包或版本范围不满足就失败。包解开后还会重新核对锁与清单依赖一致。激活使用服务实际 Python 环境逐项比对精确版本，不执行 pip，也不自动修依赖。共享 venv 在发布/回退期间变化会拒绝继续；管理员需先准备经过审查、同时兼容旧版和候选版的运行环境。不要把切回源码目录等同于回退已被外部更改的依赖。

## 服务器配置与预检

复制 `deploy/ecs/release.config.example.json` 到独立运维配置目录，填写实际绝对路径。`snapshot_root` 必须与备份服务配置相同，二者共用 `.maintenance-operation.lock` 的 OS 文件锁。锁文件永久保留；不要根据文件存在判断锁忙或删除它。进程退出后 OS 自动释放锁。

`storage_root`、`database_url`、`workflow_db`、`workflow_storage` 将与正在运行的服务环境核对。`/proc/MainPID/cwd` 也必须等于 `current` 指向目录；只移动 symlink 却未重启的状态不能冒充已部署。数据库必须是文件型 SQLite，所有配置路径必须存在并被存储根或显式 `extra_roots` 覆盖。独立配置、TLS 等需要恢复的文件应列入 `extra_roots`。应用 env 与 nginx 文件自动纳入；仅支持模板中的 `ark-video-workflow.service`、8443 nginx 单 server 块和共享 venv 布局。

将已校验的完整包和本次经过审查的发布工具代码放到独立运维目录。首次升级时不能依赖旧 `current` 中尚不存在的新工具；后续回退也继续使用这份工具，避免执行待回退版本的旧发布逻辑。工具本身不传输文件、不连接 SSH。以下命令在已准备好的 ECS 运维目录运行，配置路径和版本值需替换为实际值：

```text
python deploy/ecs/release.py inspect --config /etc/ark-video-release/config.json
python deploy/ecs/release.py check --config /etc/ark-video-release/config.json --bundle /reviewed-input/release.tar.gz --expected-revision CURRENT_ID
python deploy/ecs/release.py deploy --config /etc/ark-video-release/config.json --bundle /reviewed-input/release.tar.gz --expected-revision CURRENT_ID
python deploy/ecs/release.py verify --config /etc/ark-video-release/config.json
```

`check` 会建立独立候选目录，但不停止服务、不切换、不改数据库。预检以应用服务用户执行，使用临时数据根、禁用 dotenv、mock 模式和禁止外部网络的进程；不会继承提供商凭据或启动 lifespan worker。`inspect` 输出实际 current 身份及运行环境，不输出 env 中的秘密值。

从旧版首次迁移时，没有统一清单的目录使用实际 app/ui 字节的 `legacy-sha256:...` 身份，不能相信旧 `REVISION` 文本。需另外将已独立核实的完整提交写入 `legacy_source_commit`，用于这一次备份的代码关联；新的发布清单建立后不再依赖手填 SHA。若旧版本的额外文件或运行依赖无法核实，先停止升级，不伪造新版清单。

## 停写、快照与失败处理

发布拒绝活动队列和未完成的恢复 hold。进入维护前记录 `.release-maintenance.json`，暂停公网新请求并等待旧 nginx worker 退出，停止服务并再次确认队列。然后调用与备份相同的完整快照引擎：覆盖配置的数据根、递归 SQLite、媒体、独立配置，执行文件哈希、数据库完整性和媒体引用验证。服务停止失败不能创建“已停写”快照。

进入维护后磁盘上的 nginx 配置含 `return 503`，因此快照另有 **`nginx_before_release`** 命名根保存进入维护前的原件。发布凭据记录 `nginx_restore_root` 和 `nginx_restore_destination`。恢复代理配置应使用这份原件并验证 nginx；普通 `nginx_config` 根可能是维护状态，不能误当正常配置直接启用。数据恢复仍遵循 `docs/reliability-recovery.md` 的新目录和 restore hold 流程。

发布凭据记录前后版本、快照 ID、快照 manifest 哈希、运行依赖和恢复映射，位于 `snapshot_root/release-receipts/`。新版目录还保留副本。原始快照、代理原件和旧 release 均保留，不自动清理。

只有候选版本实际进程目录、健康检查、完整文件校验及发布凭据写入均成功，才恢复公网。任何异常先重新关闭公网再判断回退。旧服务启动或健康检查失败、存在活动任务、依赖漂移或降级不兼容时保持维护；不能把日志中的“尝试回切”当作已恢复。重新关闭 nginx 本身失败时尝试停服务，并保留恢复记录。备份及其 ExecStopPost 发现发布维护标记也会拒绝重启应用。

## 回退与恢复维护状态

```text
python deploy/ecs/release.py rollback --config /etc/ark-video-release/config.json --expected-revision CURRENT_ID
python deploy/ecs/release.py recover --config /etc/ark-video-release/config.json --expected-revision RECORDED_PREVIOUS_ID
```

`rollback` 只使用当前版本凭据记录的前一目录，并先为当前数据再做快照；从不覆盖数据库。必须保留新业务写入和已经获得的远端任务 ID。`recover` 用于失败后维护标记仍在的状态：核对记录的前一版本、依赖、队列与数据能力，重新启动并通过健康检查后才解除维护。不要手工删除维护标记来绕过检查。

向不声明对应能力的旧版本回退时，以下数据会拒绝：工作流 uncertain 或持久化 provider state、人物 stopped/uncertain、已有查询窗口记录、任意 `restore_held`。任何根的 `.restore-hold.json`（包括损坏标记）都禁止自动激活。SQL 新增列/表不等于旧 worker 理解新状态；不自动清空记录、不删除查询窗口、不重置 paid task、不恢复旧库来“解锁”回退。必须采用兼容修复版本或按恢复流程人工核对。

## 本地验证范围

测试使用临时 Git 仓库、完整包、真实临时 SQLite 和完整快照；只在 systemd/nginx、进程环境和 symlink 边界使用替身。覆盖重复构建、文件遗漏/篡改、漏依赖、未跟踪文件、配置覆盖、版本漂移、维护原件、失败回切、恢复维护状态、保留新增业务数据及降级拒绝。Windows 跳过 POSIX umask 权限案例；Linux 服务启动、nginx 排空、真实用户权限和生产规模快照仍须在正式部署前演练。
