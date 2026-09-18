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

视频链接下载依赖 `yt-dlp`。抖音链接可能受到登录、地区、反爬和平台策略影响；下载失败时请改用上传文件方式。请只处理你有权下载和再创作的视频，并确保人脸参考图获得了必要的肖像授权。

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
- `prompt`: 文本提示词。

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

## 当前 MVP 的边界

- 任务状态保存在进程内存中，服务重启后任务记录会消失；生产部署应换成 Redis/PostgreSQL 和持久化任务队列。
- 任务后台使用 Python 线程，适合本地验证；多人并发应换成 Celery/RQ worker。
- `deface` 是逐帧检测，不包含复杂的跨帧跟踪和最终视频二次人脸审计。
- 真实 Seedance API 的账号、肖像授权、内容安全审核、模型版本和费用不在本地 MVP 中自动处理。
