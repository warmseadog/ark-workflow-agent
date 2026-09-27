# 后台配置中心

入口：制作页右上角「后台配置」，或 `http://127.0.0.1:8000/admin/settings`。

## 页面与生效范围

- 配置概览：查看视频模型、TikHub、TOS 的已保存状态；“已配置”不等于远程服务验证通过。
- 视频模型：复用制作页的模型配置、测试连接及密钥保存接口。
- 链接解析：管理 TikHub API Key，和制作页「使用链接 → 链接解析设置 · TikHub」共用。
- 对象存储：管理 TOS 的启用状态、Bucket、Region、Endpoint、AK/SK、对象目录和下载链接有效期。
- 提示词模板：新增、修改、删除本机共用模板，制作页刷新后可选用。
- 打码参数：只读显示服务端默认值、当前浏览器已保存的参数及各自来源。
- 系统信息：只读显示存储目录、上传上限、轮询间隔等当前服务配置。

模型和 TOS 在提交生成任务时保存内存快照，后续修改不改变已提交任务。服务端环境参数仍需在环境配置中修改并重启生效。后台 API 和页面沿用本机访问与同源限制，当前不支持远程管理员登录。

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
参考视频 → 本地打码 → TOS 私有对象 → 临时签名下载链接 → 火山方舟
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
