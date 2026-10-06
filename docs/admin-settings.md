# 后台配置中心

入口：制作页右上角「后台配置」，或 `http://127.0.0.1:8000/admin/settings`。

## 页面与生效范围

- 配置概览：查看视频模型、TikHub、TOS 的已保存状态；“已配置”不等于远程服务验证通过。
- 视频模型：复用制作页的模型配置、测试连接及密钥保存接口。
- 链接解析：管理 TikHub API Key，和制作页「使用链接 → 链接解析设置 · TikHub」共用。
- 对象存储：管理 TOS 的启用状态、Bucket、Region、Endpoint、AK/SK、对象目录和下载链接有效期。
- 提示词模板：新增、修改、删除本机共用模板，制作页刷新后可选用。
- 人物打码：选择本地处理（默认）或外部 API，并设置新草稿的默认打码参数。高级参数按需展开，制作页参考视频下方保留打码预览。
- 系统信息：只读显示存储目录、上传上限、轮询间隔等当前服务配置。

模型和 TOS 在提交生成任务时保存内存快照，后续修改不改变已提交任务。服务端环境参数仍需在环境配置中修改并重启生效。后台 API 和页面沿用本机访问与同源限制，当前不支持远程管理员登录。

## 人物打码服务

后台「人物打码」分区可以切换处理方式。默认使用本地 deface / 本地头发遮挡处理器，无需 API Key。外部服务需填写完整处理接口地址，可选 Bearer API Key；超时时间默认 600 秒，支持 10–3600 秒。保存后无需重启，新的预览及尚未开始打码的任务使用新配置；正在处理的调用使用启动时的配置。服务切换会区分缓存，已经处理完成或已提交到视频模型的结果不会重新打码。

外部模式优先调用 API，失败、超时或结果无效时自动执行本地打码；两种方式都失败才让任务报错。回退使用原有参数和同一份输入，临时文件通过完整视频解码与时长校验后才发布，日志记录 `redaction local fallback` 及脱敏原因。

**火山 AI MediaKit**：接口地址填写 `https://mediakit.cn-beijing.volces.com`，使用 MediaKit 控制台生成的专用 API Key。程序自动申请上传地址、以二进制 PUT 上传视频、提交 `/api/v1/tools/face-blur-video`，轮询 `/api/v1/tasks/{task_id}`，然后下载并校验结果。上传、提交、查询和下载共享外部超时预算；不重复提交可能已经受理的任务。超时转本地时，已经受理的云端任务可能仍在执行。

参数映射：`style` 对应马赛克/高斯模糊；`threshold` 对应置信度；`face_box_expand=(mask_scale-1)/2`；`mosaic_size` 小于 12、12–30、大于 30 分别对应 low、medium、high，属于不同处理器的近似强度映射。云端使用自身的遮罩形状、检测分辨率和跟踪算法，本地的形状与检测尺寸不传给火山。头发模式、黑色/图片遮挡、不保留音频、mask_scale≤1 或 threshold<0.1 使用本地处理。火山仅接受不超过 10 分钟、25–60 fps、最高 4K 的视频，不符合限制时可能拒绝并触发本地兜底。

其他服务仍采用**同步上传协议**：

- 请求：`POST <接口地址>`，`multipart/form-data`。
- 文件字段：`video`，待打码的视频二进制。
- 参数字段：`options`，JSON 字符串，包含 `style`、`shape`、`mask_mode`、`mask_scale`、`mosaic_size`、`threshold`、`detection_size`、`keep_audio`、`robust_tracking`。外部实现须按约定执行这些参数。
- 鉴权：有 Key 时发送 `Authorization: Bearer <API Key>`，不把密钥放在 URL 中。
- 成功：HTTP 200，直接返回 MP4，Content-Type 为 `video/mp4` 或 `application/octet-stream`。系统检查视频可解码后才接收结果。
- 失败：非 200、超时、无效视频或超出上传大小限制时自动转本地打码，不使用原片代替打码结果。

除 MediaKit 外，其他厂商的异步任务和 JSON 下载地址仍需适配层。发型参考图的单独人脸遮挡仍由本地图片处理器完成。

配置保存在 `STORAGE_DIR/private/redaction-service.json`，仅管理员可读写；API 不回传 Key。密钥留空保留，更换接口地址后旧密钥不沿用，可在高级配置中主动清除。切换外部服务后，原始待打码视频会上传至所配置接口。

配置页移除了重复流程介绍和默认值摘要；模型生成默认值、模型目录、存储高级选项折叠显示。人物库不再提供云端名单同步、按 Asset ID 导入或云端照片浏览入口，已有本地人物与素材仍可管理。

## TikHub

1. 填写 API Key 并保存，或沿用已有的本机密钥。
2. 点击「测试连接」，通过官方 `GET /api/v1/tikhub/user/get_user_info` 验证账户鉴权。
3. 在制作页粘贴作品分享链接，点击打码预览或生成时解析并下载视频。

测试使用当前表单；留空沿用保存值，不保存测试草稿、不抓取视频。连接成功不代表所有平台权限和账户额度均满足，具体以 TikHub 控制台为准。

密钥继续保存在 `storage/local-preferences.db`（或自定义 STORAGE_DIR）。清除密钥会禁用环境变量回退。浏览器不接收已保存的密钥。

## TOS

在对象存储分区填写：

- Bucket：已创建的桶名，例如 `my-video-bucket`，不填 URL。
- Region：桶所在地域，例如 `cn-beijing`。
- Endpoint：与地域一致的官方公网 HTTPS 地址，例如 `https://tos-cn-beijing.volces.com`。目前不支持自定义域名、私网 Endpoint 或临时 STS 凭据。
- Access Key / Secret Key：有目标桶权限的 AK/SK。
- 对象目录：默认 `ark/redacted/`。
- 下载链接有效期：默认 24 小时，可设 1 小时至 7 天；应覆盖排队、拉取与必要的重试。

勾选「启用 TOS 视频上传」，保存后用于新的火山方舟真实生成任务。其他服务商沿用原有上传方式。未启用 TOS 时，方舟仍使用模型配置中的工作台公网地址。

点击「测试 Bucket 连接」只执行 HeadBucket，不上传素材。测试需要 HeadBucket 权限；真实上传还需要 PutObject / GetObject 权限。验证失败不会覆盖已保存配置。

真实生成流程：

```text
参考视频 → 本地或外部 API 打码 → TOS 私有对象 → 临时签名下载链接 → 火山方舟
```

上传仅接受 STORAGE_DIR/work 内名为 defaced.mp4 的已有打码文件，不接受原视频或任意路径。视频须在 50 MB 以内。对象名称使用随机标识，上传后保持私有，SDK 生成 GET 签名链接。TOS 无法上传时任务明确失败，不退回演示、不自动把原视频暴露到公网。

配置保存在 `STORAGE_DIR/private/storage-settings.json`，没有静态文件公开路由，AK/SK 不回传浏览器。该文件为本机明文配置，应按应用配置文件管理其系统访问权限。留空保留同一 Endpoint 的密钥；更换 Endpoint 清除旧凭据沿用，更换 AK 时需要重新填写 SK。

签名链接到期不会删除对象；请在 TOS 控制台为专用对象目录设置合适的生命周期。服务商错误中的媒体地址会被脱敏，避免签名链接进入任务日志。

## 验证

- `python -m pytest -q -p no:cacheprovider --basetemp=<项目内新的测试目录>`
- `python tests/browser_admin_settings.py`：需服务已运行及 Playwright/Edge 可用；写入配置请求在测试浏览器中拦截，不修改用户配置。
- TOS 自动化测试模拟官方 SDK 的网络边界，涵盖私有上传、签名 URL、配置快照、拒绝原视频、失败脱敏及传入方舟。
- 真实 TOS 权限、桶和网络仍需填写实际凭据后验证；自动化测试不会创建付费模型任务。

## 官方参考

- [TikHub 账户信息](https://docs.tikhub.io/186826050e0)
- [TOS Python SDK 上传](https://www.volcengine.com/docs/6349/92800?lang=zh)
- [TOS Python SDK 预签名](https://www.volcengine.com/docs/6349/135725?lang=zh)
