# 真人视频上传入口与上线记录

人物素材库原来只有照片上传入口，但服务端已经具备人物视频上传、检查和选用能力。本次在人物列表与详情页加入“添加视频”，复用已有上传与官方素材检查流程。

- 支持 MP4 / MOV；界面提示 2–30 秒、最多 50 MB、24–60 fps，建议 720p / 1080p。最终格式、时长等由服务器验证。
- 上传以 `person_video` 类型保存，再关联到选择的人物并进入检查队列。
- 显示视频数量、检查中、可用及错误反馈；已有素材重复添加沿用原有去重机制。
- 人物列表与详情页均保留 `can_manage` 权限；共享真人库由管理员维护。
- 视频仅在打开预览时创建播放器，关闭后释放视频资源。
- 桌面和手机布局沿用线上人物库详情与共享权限功能。

## 验证

本地 39 项测试通过：`test_real_person_video_ui.py`、`test_real_person_video_api.py`、`test_person_video.py`、`test_portrait_library.py`、`test_tenant_portrait_selection.py`。浏览器测试覆盖桌面列表上传、手机详情上传、只读权限、服务器拒绝上传、检查状态刷新和关闭预览释放视频。

服务器使用隔离临时数据库执行 `test_real_person_video_api.py` 与 `test_person_video.py`，8 项通过。测试使用合成视频及模拟官方接口，没有提交真实人物授权或付费生成请求。

公网健康检查、登录页均为 HTTP 200；公网返回的新 JavaScript 与发布文件逐字节一致。公网探测使用未验证证书的 TLS 上下文，不代表证书信任状态检查。

## 发布

- 地址：https://118.196.7.195:8443/people
- 新版本：`/opt/ark-video-workflow/releases/20260929T110829Z-real-person-video`
- 上个版本：`/opt/ark-video-workflow/releases/20260929T100654Z-workspace-library`
- 备份：`/opt/ark-video-workflow/data/backups/before-real-person-video-20260929T110829Z`
- 发布清单：新版本目录内 `real-person-video-release.json`
- 更新 3 个应用文件：`virtual-library.js`、`people.html`、`admin_settings.html`；另含 2 个服务器回归测试文件。
- 发布前确认没有进行中的任务，备份数据库与配置后切换版本；发布后验证历史数据和账号密码哈希未变、服务健康、匿名访问仍受限制。

发布脚本：`storage/deploy-real-person-video.py`；日志：`storage/real-person-video-deploy.log`；公网验证：`storage/real-person-video-public-verify.json`。
