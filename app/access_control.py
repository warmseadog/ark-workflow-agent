"""Deny-by-default app session boundary, before legacy or new API handlers."""
import hmac
import os
import re
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, RedirectResponse
from . import tenancy


def public_request(path, method):
    if method in {'GET','HEAD'}:
        return (path.startswith('/static/') or path in {'/healthz','/login','/auth/portrait/done'}
                or bool(re.fullmatch(r'/auth/portrait/(scan|return)/[a-f0-9]{64}',path))
                or bool(re.fullmatch(r'/api/reference-videos/[A-Za-z0-9_-]{32,128}',path)))
    return path == '/api/auth/login' and method == 'POST'


def ordinary_allowed(path, method):
    if path in {'/','/studio','/v1','/people','/videos','/account/password'}:
        return method in {'GET','HEAD'}
    if path in {'/api/auth/me','/api/auth/password','/api/auth/logout'}:
        return True
    if path.startswith(('/api/production/','/api/previews')):
        return '/legacy-' not in path
    if path == '/api/prompt-templates' or path.startswith('/api/prompt-templates/'):
        return True
    if path in {'/api/video-link/inspect','/api/video-link/import'}:
        return True
    if path == '/api/link-settings':
        return method == 'GET'
    if path == '/api/portrait/config' or path == '/api/portrait/virtual/status':
        return method == 'GET'
    if path in {'/api/portrait/people/sync','/api/portrait/people/resolve'}:
        return False
    if path == '/api/portrait/people' or path.startswith('/api/portrait/people/'):
        return True
    if path == '/api/portrait/photos' or path.startswith('/api/portrait/photos/'):
        return True
    if path == '/api/portrait/sessions' or path.startswith('/api/portrait/sessions/'):
        return True
    return False


def same_origin(request):
    if request.headers.get('sec-fetch-site') == 'cross-site':
        return False
    origin = request.headers.get('origin')
    allowed = os.environ.get('APP_PUBLIC_ORIGIN','').rstrip('/') or str(request.base_url).rstrip('/')
    return not origin or origin.rstrip('/') == allowed


def install(app, settings_getter):
    @app.middleware('http')
    async def protect(request, call_next):
        if not tenancy.enabled():
            return await call_next(request)
        from .accounts import Accounts, AccountError
        path, method = request.url.path, request.method
        def error(status,detail):
            return JSONResponse({'detail':detail},status_code=status,headers={'Cache-Control':'no-store'})
        if method not in {'GET','HEAD','OPTIONS'} and not same_origin(request):
            return error(403,'请求来源不正确，请从工作台重新操作。')
        if public_request(path,method):
            response = await call_next(request)
            if not path.startswith('/static/'):
                response.headers['Cache-Control']='no-store'
            return response
        accounts = Accounts(tenancy.config_root(settings_getter()))
        token = request.cookies.get('ark_session','')
        try:
            session = await run_in_threadpool(accounts.authenticate,token)
        except AccountError:
            session = None
        if not session:
            if path.startswith('/api/') or method not in {'GET','HEAD'}:
                return error(401,'请先登录。')
            return RedirectResponse('/login',status_code=303,headers={'Cache-Control':'no-store'})
        user = session['user']
        request.state.user, request.state.session = user, session
        if user['role'] != 'admin' and not ordinary_allowed(path,method):
            return error(403,'此操作仅管理员可用。')
        if not user.get('legacy_owner') and (path.startswith(('/api/jobs','/api/discovery','/api/workflow')) or '/legacy-' in path):
            return error(403,'历史任务属于初始管理员。')
        if method not in {'GET','HEAD','OPTIONS'}:
            csrf = request.headers.get('x-csrf-token','')
            if not csrf or not hmac.compare_digest(csrf,session['csrf_token']):
                return error(403,'安全校验失败，请刷新页面后重试。')
        context = tenancy._request_settings.set(tenancy.user_settings(settings_getter(),user))
        try:
            response = await call_next(request)
            response.headers['Cache-Control']='no-store'
            response.headers['X-Account-ID']=user['id']
            response.headers['X-Content-Type-Options']='nosniff'
            response.headers['Referrer-Policy']='same-origin'
            media_access = getattr(request.state, 'admin_media_access', None)
            if media_access and method in {'GET','HEAD'} and response.status_code < 400:
                try:
                    await run_in_threadpool(accounts.audit_media_access, user['id'], *media_access)
                except AccountError as exc:
                    return error(exc.status_code, exc.detail)
            if method not in {'GET','HEAD','OPTIONS'} and response.status_code < 400 and not path.startswith('/api/auth/'):
                accounts.audit(user['id'],method+' '+path)
            return response
        finally:
            tenancy._request_settings.reset(context)
