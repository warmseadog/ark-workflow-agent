# ECS-YRXT 独立部署记录

部署日期：2026-09-27。已开通独立公网 HTTPS 入口及 HTTP Basic 登录；已上线自动真人认证二维码与手机返回流程。

## 使用方式

公网工作台：https://118.196.7.195:8443/ 。浏览器会弹出登录框，用户名 admin，密码按用户本次指定值设置。密码明文不保存在项目文件中。

备用隧道入口：http://127.0.0.1:18081/ （此入口需要保持 SSH 隧道运行）。
原本机工作台：http://127.0.0.1:8000/ 。两者有各自独立的素材、草稿和任务。

本次已在后台启动隧道。关闭电脑或连接中断后，在本项目目录运行：

```powershell
.\connect-ecs-yrxt.ps1 -Tunnel
```

若受 VPN/代理影响，保持 VPN 开启并改用实体网卡出口：

```powershell
.\connect-ecs-yrxt.ps1 -Tunnel -BindPhysical
```

该命令保持运行期间可以使用工作台；按 Ctrl+C 只断开隧道，不停止服务器应用。18081 若被占用，可加 `-LocalPort 18082` 并访问相应地址。

只读检查服务：

```powershell
.\connect-ecs-yrxt.ps1 -Command "systemctl is-active ark-video-workflow.service"
```

SSH 使用 C:/Users/Administrator/.ssh/ecs-yrxt_ed25519，仅本机引用。身份认证限 publickey，启用严格服务器主机密钥检查。没有复制私钥。

## 隔离配置

| 项目 | 新视频工作台 | 已有 director-prompt-h5 |
| --- | --- | --- |
| 应用目录 | /opt/ark-video-workflow/current | /opt/director-prompt-h5 |
| 服务 | ark-video-workflow.service | director-prompt-h5.service |
| 用户 | ark-video-workflow（非 root） | 保持原样 |
| 监听 | 127.0.0.1:18080 | 127.0.0.1:3000 |
| 访问 | HTTPS 8443 + 密码登录；备用 SSH 隧道 | 原 Nginx HTTPS 443 网站 |

新服务设置开机自启、失败重启、单 Uvicorn worker，CPU 上限相当于 1 核，内存高水位 2 GiB、硬上限 3 GiB。数据目录 /opt/ark-video-workflow/data；环境配置 /opt/ark-video-workflow/config/app.env。

Python 3.11、虚拟环境、FFmpeg 和缺少的 libGL 动态库均位于新项目目录。动态库通过 apt-get download + dpkg-deb -x 解压，未对系统执行安装/升级。实际依赖版本记录在 deploy/ecs/requirements-linux.lock.txt。

初次隧道部署未修改 Nginx。本次新增 /etc/nginx/conf.d/ark-video-workflow.conf，监听独立 8443 端口，语法检查通过后平滑重载。原有站点配置、证书、服务及防火墙均未修改；所有部署前存在的 Nginx 配置文件和旧 systemd 服务文件 SHA-256 与基线一致。旧项目 PID 18654、Nginx 主进程 PID 7956 保持不变，原 HTTPS 网站返回 200。

## 已迁移和验证

用户明确授权后，通过 SSH 迁移以下内容：

- 模型设置（包含现有 API Key）；工作台公网地址留空。
- TOS 设置（包含已启用状态和凭据）。
- 真人素材接口设置。
- TikHub Key 和提示词模板所在 local-preferences.db（SQLite 一致性快照）。

凭据不写入源码、发布压缩包或日志。远端 private 目录权限 0700、配置文件权限 0600，由新服务用户持有。新数据目录独立，没有整体复制本机 production/workflow/studio 任务库。本次按用户要求单独迁移 1 条真实生成成功的成片及成功任务记录；原始参考素材与其他任务留在本机，不会触发重新生成。

验证通过：

- 新工作台、健康接口、配置接口、任务接口均返回 HTTP 200。
- TOS Bucket 读取检查通过（未测试上传权限）。
- TikHub 账号鉴权检查通过（未抓取作品）。
- 真人素材列表读取通过，当前 0 张；尚未测试创建认证会话或创建素材权限。
- 合成 1 秒视频以专用用户执行 face、face_hair_primary、face_hair_all，均输出 5 帧视频。
- 依赖一致性检查通过；服务完成配置后 NRestarts=0。
- 未发起付费模型生成，也未发起真实人脸认证。

服务器总内存约 8 GiB、4 核，部署后系统盘可用约 13 GiB。视频文件与旧项目仍共享系统磁盘容量；当前没有自动删除用户素材的清理策略，持续大量生产前需确定保留周期或独立数据盘。

## 公网登录和视频迁移

- 地址：https://118.196.7.195:8443/ ，无需 SSH 隧道或域名。
- 登录：HTTP Basic，用户名 admin。密码哈希在 /etc/nginx/ark-video-workflow.htpasswd，root:www-data，0640；项目不保存密码明文。
- 登录覆盖页面、静态文件、API 和视频下载；未登录/错误密码返回 401。
- Nginx 拒绝不匹配的 Origin 以及跨站写请求后，才将请求 Host 转为内部地址并清空 Origin，兼容应用原有本机访问校验。此转发方式只允许用于已经启用认证和来源检查的入口；应用继续监听 127.0.0.1:18080，不能直接对公网开放。
- 复用覆盖 IP 地址的有效 HTTPS 证书；现有 director-prompt-cert-renew.timer 及续期后 Nginx reload hook 保持原样。
- 公网直接连接验证通过，跨站写请求返回 403。
- 迁移任务：20cfc56afbf0434a919a140a67ac07f1；列表名称“已迁移成片 · 恢复上次视频素材”。
- 生成时间：2026-09-27 16:21 左右（北京时间完成）；时长约 8.04 秒，3,546,480 字节。
- 视频 SHA-256：1477bee326230165f7fdef612c0ab9afb7cc589beebdda269c8ece4530393f3b。
- 公网任务详情返回 200，视频 Range 播放返回 206，完整下载哈希与本机一致。
- 迁移前任务库备份位于 /opt/ark-video-workflow/data/backups/before-video-migration-20cfc56afbf0434a919a140a67ac07f1.db。

## 后续真人认证及访问范围

域名不是必须条件，服务器已有可公网访问的 IP HTTPS 地址。自动二维码流程已接入：创建官方会话、二维码重取、手机返回页、后端官方结果查询。点击真人认证自动出码，公网实测通过。创建认证权限已验证；真人照片自动上传入库尚未实现，现有功能仍导入官方已就绪素材。详见 docs/portrait-authorization.md。

当前共享账号适合简单访问控制，并不是公司网络白名单。若以后需要仅允许办公室网络访问，需要提供公司的固定公网出口 IP；若需要员工独立账号，再设计账号与素材隔离。

不需要重新提供服务器、SSH 私钥、模型 Key、TOS Key 或 TikHub Key。创建认证权限已通过真实会话创建验证；创建图片素材的权限尚未进行写入验证。

## 后续运维

- 只重启新项目：systemctl restart ark-video-workflow.service。
- 查看新项目日志：journalctl -u ark-video-workflow.service。
- 停止新项目：systemctl stop ark-video-workflow.service；不影响 SSH 或旧服务。
- 更新代码应新建 releases/<版本>，检查后切换 current；数据持续位于 data。不要覆盖 data 或旧项目路径。
- 本次原服务基线和验证摘要在本机 storage/ecs-deploy 下（Git 已忽略）。

自动认证发布版本：/opt/ark-video-workflow/releases/20260927-portrait-auto。更新 private 配置文件时必须保留 ark-video-workflow 用户归属和 0600 权限，避免服务无法读取。


## 2026-09-27 轻量人物与照片校验发布

- 修改前 Git 基线：c057498；功能提交：bb8ac501d7b91fcb0163c4f208b12ae67f3b2dd8。
- 当前 release：/opt/ark-video-workflow/releases/20260927-portrait-library，REVISION 文件记录功能提交。
- 回退版本：/opt/ark-video-workflow/releases/20260927-portrait-auto。仅切换 current 并重启 ark-video-workflow.service 即可；本次数据库只新增表，旧代码兼容。
- SQLite 一致性备份：/opt/ark-video-workflow/data/backups/before-portrait-library/，包含 production.db 和 portrait-sessions.db。未修改凭据、Nginx、旧网站或旧服务。
- 新增本地人物目录、备注、人物图片缩略图、后台照片校验和去重记录。现有1个认证人物已自动迁移，可显示“已认证 · 可上传第一张照片”。
- 发布验证：302项 pytest 通过；桌面1440及手机390浏览器多人选择/改名/刷新/普通模式/慢上传移除/任务快照通过；认证二维码回归通过。
- 公网页面、人物列表、任务接口和脚本均200；匿名人物接口401；跨站写请求403；浏览器无JS错误，手机无横向溢出。
- 新服务 active/running、NRestarts=0；旧 director-prompt-h5 PID仍为18654、网站200；Nginx全部现有配置指纹不变；已有迁移成功视频仍保留。
- 本次未上传真实人脸照片或调用付费视频模型。照片处理使用官方 CreateAsset / GetAsset，真实照片首次入库权限及一致性结果应以用户实际上传后的官方状态为准。


## 2026-09-27 任务来源展示修复

- 功能提交 1ec3063，当前 release 为 /opt/ark-video-workflow/releases/20260927-task-sources；可回退到上一版 20260927-portrait-library。
- 用户要求查看已迁移视频三个来源。本地核对原成功任务，补传对应 source.mp4、face-01.jpg、clothing-01.png 三份文件，SHA-256 与原记录一致；仅恢复该任务 snapshot 中三类素材引用，未修改当前草稿、生成状态或再次调用模型。
- 修改前任务库备份：/opt/ark-video-workflow/data/backups/before-task-source-restore.db。源码和素材包分开传输，用户素材不进入 Git。
- 普通用户展开任务详情后只显示创建时间和参考视频、人物参考图、衣服参考图；去掉编号、服务商原始错误及参数 JSON。没有来源的历史记录显示缺失提示。来源媒体仅在展开后设置地址。
- 验证：21项相关接口/布局测试、1440/390浏览器回归通过；公网实际展开验证三份来源哈希一致、两张图片已加载、视频地址可访问，技术字段隐藏，无JS错误，无手机横向溢出。
- 旧 director-prompt-h5 进程和 Nginx 配置指纹保持不变，新服务 active、NRestarts=0。


## 2026-09-27 打码预览小按钮

- 功能提交 a511ba3；当前 release /opt/ark-video-workflow/releases/20260927-redacted-preview，上一版 20260927-task-sources 保留。
- 任务详情加入“预览打码效果”按钮，点击后在页内展开播放器，再点收起；收起或关闭详情会暂停，点击前不加载视频，刷新任务状态时尽量保留已打开的播放器。
- 补传任务 20cfc56afbf0434a919a140a67ac07f1 的本地原有 defaced.mp4，1,399,813字节；不重新打码、不调用模型，原有三个来源和成片均保留。
- 验证：1440/390浏览器回归通过；公网实际播放720×1280、约8.52秒的打码视频，收起暂停、移动端无溢出，下载哈希与本地原文件一致，无JS错误。
- 原网站进程与Nginx配置指纹不变，新服务active、NRestarts=0。


## 2026-09-27 场景及发型参考图

- 功能提交 dc276cb4c4de78c841386dfb9671593d0cba4586，分支 codex/scene-hairstyle。
- 当前 release：/opt/ark-video-workflow/releases/20260927-scene-hairstyle；上一版 20260927-redacted-preview 保留。私有任务库备份 data/backups/before-scene-hairstyle.db，服务用户归属、0600。
- 四栏：视频、衣服、人物、可收起的场景图；人物内可展开上传一张发型图。两种新参考图均可暂时停用，收起不影响启用状态。刷新恢复素材、开关和场景补充，打开状态默认收起。
- 可选素材参与任务不可变快照和复制，详情展示来源及“未使用”标记。仅启用时提交实际图片；动态图片编号和分工指令适用于所有模板，旧用户自定义模板不被覆盖。真人授权仍仅绑定人物图，额外图片仍受模型素材检查。
- 本地验证：309 项 pytest 通过，1 条既有 Starlette 弃用提示；旧流程 1440/390 浏览器验证、新流程 1440/900/390 验证通过。独立代码审查无阻塞问题。
- 公网验证：1440/390 新控件、折叠、四栏、无横向溢出、原三份来源、打码视频实际播放/暂停通过，无 JS 错误。匿名请求401、跨站写入403、原网站200。
- 仅重启 ark-video-workflow.service，active/running、NRestarts=0；原 director-prompt-h5 PID仍18654，全部已记录Nginx配置指纹不变。
- 本次没有提交新的付费视频生成任务；最终发型/场景的生成视觉效果仍以实际模型结果为准。


## 2026-09-27 素材目录写入权限修复

- 线上链接导入在解析、下载后，register/copyfile 写入 data/assets 时抛出 PermissionError。目录实际为 root:root 0755，服务账号无法新建文件；此前迁移原任务素材只确保了文件归属，没有确保父目录归属。
- 原地修正 /opt/ark-video-workflow/data/assets 为 ark-video-workflow:ark-video-workflow 0700；不递归修改既有文件，不重启服务，不改 Nginx。backups 继续 root 私有。
- 修复前以服务账号写入确认失败；修复后服务账号创建/读取/删除通过。实际运行的素材上传 API 返回200，读取文件字节一致；仅本次随机命名测试素材及其数据库记录被清理。
- 两个服务进程未变化：新项目217393、原项目18654，均active。本次未再次调用付费链接解析或视频生成。
- 后续迁移或部署须检查服务用户对 assets/imports/uploads/work/outputs/cache 的实际写入能力；创建数据目录时设置服务用户归属，不能仅对导入文件 chown。不要修改 backups/private 的私密边界。


## 2026-09-27 提示词随素材可见联动

- 功能提交 e1707bf8504a3a94d6d03cf98c065188a3ba385d；当前 release /opt/ark-video-workflow/releases/20260927-prompt-sync，上一版 scene-hairstyle 保留。
- 未修改的四个内置模板升级为动作复刻、自然换装、电商展示和一致性约束；保留用户改过或已删除的模板。模板正文按当前人物与服装分工编写，可见“素材联动”段随发型、场景上传/删除/启用及模板切换更新，图片编号动态计算。
- 自动保存不改写正在输入的文本或移动光标；离开输入框后同步自动段。复制旧任务后重新生成的可见段会保存进新草稿及提交快照。可复用模板去除具体自动编号；后端按快照重建分工。
- 发布前备份 production.db、local-preferences.db，位于 data/backups/before-prompt-sync-*.db，0600。以服务账号验证 assets/imports/uploads/work/outputs/cache 可写。
- 已检查当前草稿 044211181a3f4c5687269e6f8346ed8a，1人物图、1衣服图、1启用发型图、无场景图。仅在正文仍为未改过的旧内置模板时按版本更新；素材与开关全部保留。公网打开后已保存 Image3 发型联动段，revision25。
- 验证：312pytest通过；新联动1440/390、原流程1440/390浏览器回归通过；独立审查发现的光标、复制保存和正文保留问题均已修复并复查。
- 公网1440/390验证当前草稿可见提示词与服务端保存一致、4份素材不变、历史任务快照不变，无JS错误/横向溢出；匿名401、原网站200。原服务PID18654与Nginx指纹不变，新服务active、NRestarts0。
- 未提交新的模型生成任务。


## 2026-09-27 六类配饰、常驻场景与虚拟人物库

- 功能提交 e26beff，官方素材下载域名修复 3a781b0。当前 release 为 /opt/ark-video-workflow/releases/20260927-accessories-virtual；上一版 20260927-prompt-sync 保留。仅部署代码，备份位于 data/backups/before-accessories-virtual/，含 production.db、local-preferences.db、portrait-sessions.db。
- 衣服参考的更多搭配为包包、帽子、手表、鞋子、项链、眼镜。场景始终展开但可选；草稿恢复、停用保留、任务快照、动态图片编号和 9 张上限均已接入。
- 虚拟人物以 AIGC 与真人 LivenessFace 分开管理，接入官方创建、TOS 上传、状态查询、已有素材导入；后台展示配置、连通状态与实际生成成功记录，照片可用不等于出片成功。
- 331 项 pytest 通过；测试环境及真实云端 1440/390 浏览器验证通过，无 JS 错误、移动端无横向溢出。匿名 API 401；服务数据目录 uid998，业务目录0700、cache0755。原 director-prompt-h5 PID18654 未变。
- 首次发布前发现用户任务 b1171d2b78674832bf4cbf63f773800b 正在运行，停止部署，待其于北京时间22:14成功后才发布。该成功任务不是新虚拟人物库验收。
- 实测人物“虚拟人物 · 当前参考角色”，本地ID 15d808a1a89237c488e4ae2dde6f5a92；官方组 group-20260927221559-mv8zc；官方素材 asset-20260927221602-f78sd。官方查询确认 AIGC / Active / default / Image。
- 独立复制草稿“虚拟人物库接入验收 · 4秒”，原用户草稿未替换。首次 run ce9db0f26f8b493e87756588b1f5a46d；间隔约6分钟后重试 run 59c14e466afc4524ad03073e2bafc85a。两次均在 submitting 阶段 HTTP400，provider_task_id 为空，模型未受理生成。
- 两次错误均为 InvalidParameter: The parameter `content[1].image_url.url` specified in the request is not valid: The specified asset asset-20260927221602-f78sd is not found.
- 首次 Request ID：021790518651843640844006c495b97e3327b5d9afec85689114b；第二次：021790518941161efca7158d8a072a352f4e6ec3154e35a91d054。
- 用户确认 AK/SK 与模型 API Key 为同一账号。官方文档说明 API Key 按资源项目隔离；项目不一致目前仅为待验证假设，不能据此断言根因。项目核对来源：https://docs.volcengine.com/docs/ark/manage-api-keys?lang=zh 。实际出片验收未通过，不应标记已保证生成。
- 自动审批拒绝了 ListApiKeys 账号关联诊断，该命令没有执行；不继续探查密钥列表。后续只核对已知素材状态、非敏感配置和用户提供的项目名称。


## 2026-09-28 虚拟人物库交互简化

- 已定向更新当前 release 的 virtual-library.js、virtual-library.css 及 production/studio/admin_settings 三个模板。发布前确认服务器文件与本地 HEAD 内容一致（仅换行符差异），没有覆盖其他页面改动。
- 默认显示人物、可用照片状态、当前素材项目；创建、已有素材导入及 ID 说明按需展开。“同步官方 AIGC 素材组”改为“读取云端人物列表”，明确只读取名单；照片需单独导入。
- 旧文件备份：/opt/ark-video-workflow/code-backups/20260928T052634Z-virtual-library-simple。资源版本 20260928-simple-2。
- 桌面 1440 / 手机 390 浏览器流程通过，覆盖创建、选择、上传、Active、列表同步不重复创建、照片导入及后台检查；导入弹窗无横向溢出。
- 服务器实际返回的首页、后台模板及两份静态资源验证通过，静态资源字节与本地一致。服务 active，PID 220850 未变；无需重启。未修改人物、任务、凭据或存储配置，未提交生成任务。本次未通过已登录公网浏览器复验。


## 2026-09-28 虚拟人物直接管理

- 已发布 /opt/ark-video-workflow/releases/20260928T053725Z-virtual-management，上一版本 /opt/ark-video-workflow/releases/20260927-accessories-virtual 保留；数据库备份 /opt/ark-video-workflow/data/backups/before-virtual-management-20260928T053725Z/production.db（0600）。
- 顶部常驻新增人物按钮；人物卡片直接提供使用、添加照片、改名、移除。上传在弹窗内完成，并显示检查进度，检查通过后自动启用使用；使用会再次通过官方导入校验再加载到制作台。
- 移除为当前账号连接下的本地隐藏，需要卡片内二次确认；不删除人物、照片、云端组或历史任务。新增 portrait_hidden_people 表保存移除状态，普通刷新和云端名单同步不会恢复，用户可从已移除人物恢复。
- API 新增 DELETE /api/portrait/people/{ident}、POST /api/portrait/people/{ident}/restore、GET /api/portrait/people/{ident}/reference。移除/恢复仅支持 AIGC 并按当前账号连接隔离。
- 当前应用 tests 目录 333 项测试通过；根目录直接 pytest 被已有 video-production-module/tests/conftest.py 副本引发的 ImportPathMismatchError 阻断，未改动该独立副本。1440/390 浏览器流程覆盖新增、改名、弹窗直接上传、检查完成、使用、刷新、取消移除、确认移除、同步保持移除、恢复与官方导入；无 JS 错误与横向溢出。
- 发布前生成队列与照片队列均为空；只重启 ark-video-workflow.service。服务器首页、后台、静态文件字节与三个新接口的 OpenAPI 校验通过；已有 5 位人物的 ID、名称、可用照片数一致。未操作现有人物移除/改名，未提交付费生成；原 director-prompt-h5 PID 18654 保持不变。


## 2026-09-28 人物选择框加宽

- 选择入口占满人物参考栏，下拉面板独立于窄栏：桌面 420px，手机最大屏宽减 24px，自动贴合视口并控制高度，人物列表独立滚动。
- 常驻搜索框、真人/虚拟人物切换；每项仅名称、照片状态及已选标记。去掉生成历史长说明；虚拟页签提供虚拟管理入口，不显示真人认证入口。
- 1440/390 人物选择浏览器回归与虚拟人物管理浏览器回归全部通过，含搜索、页签入口、宽度/越界检查、选择、改名、上传、移除恢复。
- 发布五个前端/模板文件，备份 /opt/ark-video-workflow/code-backups/20260928T054430Z-person-picker，静态版本 20260928-picker-1。服务器首页与静态内容验证一致，服务 PID 255571 不变，无重启、无业务数据变更。

## 2026-09-28 人物管理迁入后台与指定素材导入

- 发布版本 `/opt/ark-video-workflow/releases/20260928T061218Z-backend-people`；前版本 `/opt/ark-video-workflow/releases/20260928T053725Z-virtual-management` 保留，数据库备份 `/opt/ark-video-workflow/data/backups/before-backend-people-20260928T061218Z/production.db`（0600）。首次发布因将官方编号数等同于去重照片数导致校验失败，已自动回退；确认去重原因后重新发布通过。
- 后台新增 `/admin/settings#people` 人物库：真人/虚拟分类、按 Asset ID 导入并命名、照片添加、改名、本地移除与恢复，连接参数折叠展示。制作页仅选择人物、按名称搜索和后台入口；点击已有可用照片的人物会再次核实并加载照片。有可用照片的人物优先展示。
- 真人与虚拟人物均可读取当前账号下的可用参考照片、本地移除和恢复，云端素材及历史任务保留。配置仍按当前账号与项目隔离。
- 使用服务器现有 `FANGZHOU-AI` 凭据逐项 GetAssetGroup/GetAsset 核查，导入用户指定的 5 组 11 个 Asset ID。yoyo 3 个编号对应 2 张不同图片（7x5kr 与 c5nds 内容相同）；番茄虚拟人像库、yoyo生图素材、yoyo-真人头像、番茄-真人头像各 2 张。11 个编号绑定全部存在，人物列表按图片内容去重共 10 张。导入前备份 `/opt/ark-video-workflow/data/backups/before-named-people-import-20260928T060813Z/production.db`。
- 应用测试 334 项通过；1440/390 浏览器验证后台新增、改名、上传、移除/恢复、按类型与编号导入真人及虚拟照片、制作页按名称选用、草稿恢复和任务隔离，无 JS 错误。服务器前后台页面、10 个发布文件、5 组人物及参考照片接口验证通过。
- 发布前确认生成与照片处理队列空闲，只重启 ark-video-workflow.service；director-prompt-h5.service PID 未变。未新建云端素材、未迁移项目、未提交视频生成，Active 不视为已核实模型生成权限或真人授权用途。

## 2026-09-28 先选人物，再选照片

- 发布 `/opt/ark-video-workflow/releases/20260928T071742Z-person-photos`，上一版本 `/opt/ark-video-workflow/releases/20260928T061218Z-backend-people`；备份 `/opt/ark-video-workflow/data/backups/before-person-photos-20260928T071742Z/production.db`（0600），发布前生成与照片队列均空闲。
- 制作页选择人物后展示该人物的照片缩略图，点击“使用这张”才更新草稿；已选人物下常驻“选照片”。选择框取消不会更换人物或参考图。同一人物换主图保留其他不同参考图；切换人物时移除旧人物参考图。
- 照片面板可直接上传新照片，复用既有队列自动入库检查。未通过检查的照片不可选；处理中可关闭，下次打开继续查看；相同图片内容复用既有记录。提供刷新和失败/待确认后的重新检查。配置、编号导入和人物管理仍在后台。
- 新增 GET /api/portrait/people/{ident}/photos，仅查询当前凭据范围下此人物的本地照片记录，返回本地预览路径及状态，不向前端暴露私有文件路径或云端签名地址。选用时继续通过官方接口核实照片。
- 336 项应用测试通过。1440/390 浏览器验证挑选不同照片、取消、草稿恢复、官方校验失败保留原图、上传/等待检查/重开/复用、重复上传去重、跨人物清理旧参考图；后台管理回归及制作任务快照、延迟上传隔离回归通过，无 JS 错误或横向溢出。
- 服务器首页、新静态资源及 5 组照片列表发布后验证通过，共更新 9 个文件；仅重启 ark-video-workflow.service，其他服务未改变。线上未上传新素材、未改动现有草稿、未提交生成任务。

## 2026-09-28 发型参考图独立人脸打码

- 发布 `/opt/ark-video-workflow/releases/20260928T073002Z-hairstyle-mask`，保留前版 `/opt/ark-video-workflow/releases/20260928T071742Z-person-photos`，数据库备份 `/opt/ark-video-workflow/data/backups/before-hairstyle-mask-20260928T073002Z/production.db`（0600）。发布前任务与照片队列空闲，仅重启当前工作流服务。
- 发型参考图默认始终执行脸部马赛克：mask_scale=1.0、threshold=0.2，与视频 1.4 的参数独立。使用已有 CenterFace/OpenCV 检测器，椭圆范围内打码以减少对周围头发覆盖；允许折叠调整倍数与检测阈值。
- 新增 hairstyle_mask 草稿/任务快照字段，无数据库表迁移；旧草稿缺省仍按 1.0/0.2 处理。上传、替换、调整参数后自动预览，原图保留，缓存键包含原图哈希、参数与处理版本。生成使用同一份处理后的 PNG；检测/处理异常会阻断提交，无原图回退。未检出人脸明确提示用户检查预览。
- 新增 POST /api/production/hairstyle/preview 与 GET /api/production/hairstyle/preview/{key}，限制输入类型、参数、图片尺寸及缓存文件名；仅返回本地预览 URL，不暴露源文件路径。
- 343 项测试通过；桌面/手机验证自动预览、1.0 独立默认、微调、禁用/重启用和刷新恢复；发型/场景提示词联动与快照回归通过。像素测试验证默认脸框外和椭圆角落保持不变，缓存按参数分离，源图未改，处理失败不会提交原图。
- 使用线上服务实际环境加载检测器成功。发布后用服务器最近一张已有发型图调用预览，检出 1 张人脸，返回有效 PNG，参数 1.0/0.2，源文件哈希未变。未提交视频生成任务。


### 2026-09-28 人物视频参考

- 发布目录：`/opt/ark-video-workflow/releases/20260928T081635Z-person-video`。
- 回滚目录：`/opt/ark-video-workflow/releases/20260928T073002Z-hairstyle-mask`。
- 数据库备份：`/opt/ark-video-workflow/data/backups/before-person-video-20260928T081635Z/production.db`。
- 人物参考支持图片／视频切换。视频按所选人物入库，官方 Active 后可选；上传检查、复用、预览、草稿保存／恢复、任务详情均支持。仅移除本次选择时保留库内视频。
- 接入当前官方 Ark Seedance 2.0：MP4/MOV、2–15 秒、单段 50 MB 内，动作与人物视频总时长不超过 15 秒。人物视频使用 `AssetType=Video` 及 `asset://` 引用作为 Video2；动作视频为 Video1，继续打码。人物视频不打码；衣服与可选图片编号随模式调整。
- 人物素材新增 `kind` 字段与 `video_count`，保留照片默认行为。视频模式只校验／提交视频身份参考，不提交草稿内保留的闲置人物照片。
- 验证：350 项 pytest 通过；1440/390 浏览器人物视频与原照片流程通过；服务器健康、静态文件、5 组原人物资料核查通过；服务环境读取 3 秒 H.264 视频成功、无效视频上传返回 422。
- 官方素材服务在本地测试中使用模拟响应；本次上线未创建云端视频资产、未提交付费生成任务。首次真实人物视频的官方一致性检查和生成效果须以用户实际上传后的返回为准。
