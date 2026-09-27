# 视频人脸打码 + Seedance 工作流

这是一个本地可运行的 MVP：

1. 上传视频文件，或填写一个视频链接；
2. 用 [ORB-HD/deface](https://github.com/ORB-HD/deface) 对视频中的人脸做马赛克打码并保留音频；
3. 在页面中预览打码后的视频，确认效果后再进入下一步；
4. 上传人脸参考图、服装参考图和生成提示词；
5. 交给 Seedance 适配器生成；
6. 通过浏览器查看进度并下载结果。

页面中的“打码设置”可以按任务展开配置：

- 打码样式：马赛克、高斯模糊、黑色遮挡、图片覆盖；
- 遮罩倍数：控制覆盖区域大小；
- 默认遮罩倍数：1.4；页面会把你手动调整的打码设置保存在浏览器中，下次打开时自动恢复。
- 马赛克块大小：控制马赛克颗粒粗细；
- 检测阈值：控制人脸检测灵敏度；
- 检测尺寸：在速度和小脸检测能力之间取舍；
- 是否保留音频；
- 模糊模式可以选择椭圆或矩形遮罩；图片覆盖模式需要额外上传覆盖图片。

这些选项只作用于当前任务，不会修改 `.env` 中的默认值，也不会影响其他任务。


### v2 头发遮挡模式

v2 在保留 ORB-HD/deface 人脸打码的同时，接入了本地的头发语义分割和视频跟踪。页面的“遮挡目标”可以选择：

- `face`：只遮人脸，使用现有 deface 流程；
- `face_hair_primary`：遮主人脸和主人物头发；
- `hair_primary`：只遮主人物头发；
- `face_hair_all`：遮画面中所有人物的人脸和头发。

后三种模式使用本地 MediaPipe 分割模型 `storage/attached_local_face_mosaic_v3/local-face-mosaic-tracking/models/selfie_multiclass_256x256.tflite`，并通过 OpenCV/MediaPipe 脚本逐帧处理。头发模式当前使用马赛克样式，主人物模式可以开启稳定跟踪来应对转身和短暂漏检；所有人物模式使用 YuNet 模型 `face_detection_yunet_2023mar.onnx`。

头发模型会生成头发区域掩码，再对掩码做少量边缘扩张和连通区域过滤，因此长发、马尾和发髻可以沿实际轮廓处理，不需要把人脸椭圆整体放大。处理过程在本机完成，视频仍保存在 `storage/work/<job_id>`，不会因为头发模式上传到外部服务。

当前默认 `SEEDANCE_MODE=mock`。在 mock 模式下，应用会把打码后的文件作为演示结果，页面会明确标注“演示结果”，不会冒充 Seedance 生成结果。这让你可以先验证上传、下载、deface 和任务状态流程。

## 本地运行

需要 Python 3.11+。在 Windows PowerShell 中执行：

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
.\run.ps1
```

打开 http://127.0.0.1:8000 。如果 PowerShell 阻止脚本执行，可以直接运行：

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

视频链接支持小红书、抖音、快手和 B 站的完整分享文案，自动提取 URL 后通过 TikHub 解析下载。点击「使用链接 → 链接解析设置」保存 TikHub API Key，或配置服务端 TIKHUB_API_KEY。其他链接保留 yt-dlp 通用下载。提示词模板支持新增、改名、编辑、保存和删除，详情见 [链接与模板说明](docs/tikhub-and-prompt-templates.md)。

TikHub 配置独立于视频生成模型。尚未配置密钥时页面会提示补充配置；鉴权、余额、作品不可访问等问题会显示错误。链接识别本身不调用付费接口。

## 真实 Seedance 适配器

默认 mock 模式不调用外部模型。要启用当前项目内置的通用 HTTP 适配器，在 `.env` 中设置：

```dotenv
SEEDANCE_MODE=http
SEEDANCE_API_URL=https://your-seedance-adapter.example.com/v1
SEEDANCE_API_KEY=replace-with-your-key
```

适配器约定如下：

### 提交任务

`POST {SEEDANCE_API_URL}/generations`

使用 `multipart/form-data`：

- `video`: 打码后的视频文件；
- `face_image`: 人脸参考图（可选）；
- `clothing_image`: 服装参考图（可选）；
- `hairstyle_image`: 独立发型参考图（可选，仅工作台启用时发送）；
- `scene_image`: 独立场景参考图（可选，仅工作台启用时发送）；
- `prompt`: 文本提示词。

自定义适配器如需支持发型或场景参考，应读取这两个新增文件字段；未启用时不会发送。模型提示词包含各图片的用途和动态编号。

响应至少需要返回：

```json
{"task_id": "your-task-id"}
```

### 查询任务

`GET {SEEDANCE_API_URL}/tasks/{task_id}`

成功时返回：

```json
{"status": "succeeded", "output_url": "https://.../result.mp4"}
```

处理中可以返回 `queued`、`running`、`processing` 等状态；失败返回 `failed` 或 `error` 并携带 `error` 字段。

这是一个适配层协议，不是对某个具体 Seedance API 字段的硬编码。火山引擎/方舟的真实字段、鉴权方式、素材上传方式和版本能力应以你开通的官方接口文档为准；将它们映射到上面的 `SeedanceClient` 即可，不需要修改浏览器页面或任务 API。

首页、`/v1` 和 `/studio` 现均为独立制作页面，暂不接入素材发现、成功案例和制作队列导航。制作页使用独立模板与脚本，不加载这些模块的数据。

制作工作台右下角并排放置打码设置与视频模型设置，提供打码预览，以及火山方舟、toapis.cn 和自定义配置预设，支持在本机保存地址、Key、模型和输出参数。主页面展示视频、衣服和人物三个素材入口，生成前自动完成打码。模型面板提供“测试连接”，真实生成已按服务商协议接入，失败不会回退演示。火山方舟可在后台启用 TOS 上传打码视频，或配置工作台公网地址；toapis.cn 支持直接上传打码视频。详细说明见 [视频模型配置](docs/model-settings.md)。

## 目录结构

```text
app/
  main.py          FastAPI 路由和上传接口
  config.py        环境变量配置
  jobs.py          任务状态和后台流水线
  media.py         yt-dlp 下载与 deface 调用
  seedance.py      mock/HTTP Seedance 适配器
  templates/       浏览器页面
  static/          页面脚本和样式
storage/
  uploads/         预留的上传目录
  work/            每个任务的中间文件
  outputs/         最终下载文件
```

## 素材发现工作台

打开 `http://127.0.0.1:8000/` 进入女装视频素材发现工作台；原有打码页面仍可从 `http://127.0.0.1:8000/v1` 打开。

当前没有接入抖音或小红书数据服务，因此发现页不会伪造热门结果。它提供三条可运行路径：

1. 输入主题，依据案例标签生成抖音和小红书的真实搜索入口；
2. 粘贴平台链接，服务端做来源校验、规范化和去重；
3. 人工审核候选，审核通过后创建现有 v1 打码任务。

候选和案例默认保存在 `storage/studio.db` 的 SQLite 数据库中。设置 `DATABASE_URL` 可以切换到 PostgreSQL，例如：

```dotenv
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/fashion_lab
```

发现 API：

- `GET /api/discovery/candidates`
- `POST /api/discovery/import`
- `POST /api/discovery/runs`
- `GET/POST /api/discovery/cases`
- `POST /api/discovery/candidates/{candidate_id}/review`

通过审核的候选提交到 `/api/jobs` 时携带 `candidate_id`。页面会跳转到 `/v1#job=<job_id>`，原 v1 页面会恢复并轮询同一个打码任务。平台数据服务接入后，只需要新增来源适配器，不需要改审核和打码流程。

## 当前 MVP 的边界

- 任务状态保存在进程内存中，服务重启后任务记录会消失；生产部署应换成 Redis/PostgreSQL 和持久化任务队列。
- 任务后台使用 Python 线程，适合本地验证；多人并发应换成 Celery/RQ worker。
- `deface` 是逐帧检测，不包含复杂的跨帧跟踪和最终视频二次人脸审计。
- 真实 Seedance API 的账号、肖像授权、内容安全审核、模型版本和费用不在本地 MVP 中自动处理。

## 后台配置中心

制作页右上角的「后台配置」进入 [/admin/settings](http://127.0.0.1:8000/admin/settings)。可集中管理视频模型、TikHub、TOS 对象存储、提示词模板，并查看打码参数与系统信息。TikHub 与制作页共用已有密钥；启用 TOS 后，方舟使用打码视频的临时下载链接，无需工作台公网地址。详细配置与验证见 [后台配置说明](docs/admin-settings.md)。
