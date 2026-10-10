"""Development-only ASGI entry point. Production keeps using app.main:app."""
import html
import os

from tools.dev import install_network_guard

profile = os.environ.get('APP_LOCAL_DEV_PROFILE')
if profile not in {'demo', 'integration'} or os.environ.get('APP_HOST') != '127.0.0.1':
    raise RuntimeError('Use run.ps1 or python -m tools.dev run for local development.')
if profile == 'demo':
    install_network_guard()

from app.main import app
from fastapi.responses import HTMLResponse, JSONResponse


@app.middleware('http')
async def development_capabilities(request, call_next):
    path = request.url.path
    cloud_settings = {'/api/model-settings','/api/model-catalog','/api/storage-settings',
        '/api/link-settings','/api/continuation-settings','/api/variation-settings',
        '/api/inspiration-settings','/api/redaction-service'}
    cloud_actions = {'/api/production/assets/import','/api/video-link/import',
        '/api/production/inspiration-assist','/api/discovery/import','/api/discovery/runs',
        '/api/jobs'}
    if profile == 'demo' and request.method not in {'GET','HEAD','OPTIONS'} and (
        path in cloud_settings or path in cloud_actions or
        path.startswith(('/api/portrait/', '/api/workflow/')) or path.endswith('/test')):
        return JSONResponse(status_code=409,content={'detail':
            '当前是本地演示模式，此功能需要测试服务配置。请用 run.ps1 -Profile integration 启动，'
            '在独立联调后台填写测试凭据。演示模式可直接上传本地素材。'})
    response = await call_next(request)
    response.headers['X-Development-Profile'] = profile
    return response


@app.get('/__dev', response_class=HTMLResponse, include_in_schema=False)
def development_home():
    return HTMLResponse('''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
    <title>本地开发</title><h1>本地开发 · ''' + html.escape(profile) + '''</h1>
    <p>''' + ('演示模式：无云端调用。视频结果是打码后的演示文件，不是 AI 生成。' if profile == 'demo'
               else '联调模式：只使用独立测试凭据。真实 API 调用可能计费。') + '''</p>
    <p><a href="/">打开制作台</a> · <a href="/admin/settings">后台配置</a> · <a href="/docs">API 文档</a></p>
    <p>本地支持上传素材、编辑草稿、四种打码、演示任务和结果下载。</p>
    <p>AI 灵感、拍法规划、续写、人物入库和社交链接解析需要 integration 配置。
    真人认证还需要公网 HTTPS 回调；可在 test 验收。</p>
    <p>环境检查：<code>.\\run.ps1 -Check</code>；完整本地冒烟：<code>.\\run.ps1 -Smoke</code></p>
    <p>当前数据目录：<code>''' + html.escape(os.environ['STORAGE_DIR']) + '''</code></p></html>''')
