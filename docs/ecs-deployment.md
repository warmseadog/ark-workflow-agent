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
