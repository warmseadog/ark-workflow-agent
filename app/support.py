"""Private, bounded support inbox. No external messaging or billing integration."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import ipaddress
import math
from pathlib import Path
import re
import sqlite3
from typing import Literal
import unicodedata
import uuid

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import input_limits as limits, tenancy
from .access_control import same_origin
from .media_validation import IMAGE_FORMATS, VIDEO_SUFFIXES, MAX_DIMENSION, MAX_PIXELS
from .security import configured_secrets, safe_error

MAX_OPEN_PER_OWNER = 20
MAX_RECORDS = 10000
MESSAGE_MAX_CHARS = 4000
TASK_NAME_MAX_CHARS = 160
REQUEST_ID_MAX_CHARS = 64
PAGE_SIZE_MAX = 50
BODY_MAX_BYTES = 32 * 1024


class _Input(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, str_strip_whitespace=True)


class Submit(_Input):
    message: str = Field(min_length=1, max_length=MESSAGE_MAX_CHARS)
    task_name: str = Field(default='', max_length=TASK_NAME_MAX_CHARS)
    request_id: str = Field(default='', max_length=REQUEST_ID_MAX_CHARS)

    @field_validator('request_id')
    @classmethod
    def safe_identifier(cls, value):
        if value and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,63}', value):
            raise ValueError('invalid request identifier')
        return value


class Reply(_Input):
    status: Literal['open', 'resolved']
    reply: str = Field(max_length=MESSAGE_MAX_CHARS)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


class SupportStore:
    def __init__(self, root):
        private = Path(root)/'private'
        private.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = private/'support.db'
        with self.connection() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS support_requests (
                id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, username TEXT NOT NULL,
                message TEXT NOT NULL CHECK(length(message) BETWEEN 1 AND 4000),
                task_name TEXT NOT NULL CHECK(length(task_name)<=160),
                request_id TEXT NOT NULL CHECK(length(request_id)<=64),
                status TEXT NOT NULL CHECK(status IN ('open','resolved')),
                reply TEXT NOT NULL CHECK(length(reply)<=4000),
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                replied_by TEXT NOT NULL DEFAULT '');
                CREATE INDEX IF NOT EXISTS support_owner ON support_requests(owner_id,created_at);
                CREATE INDEX IF NOT EXISTS support_status ON support_requests(status,created_at);''')
        self.path.chmod(0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def create(self, actor, payload):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            count = db.execute("SELECT COUNT(*) FROM support_requests WHERE owner_id=? AND status='open'",(actor['id'],)).fetchone()[0]
            if count >= MAX_OPEN_PER_OWNER:
                raise HTTPException(429, '待处理反馈较多，请等待管理员处理后再提交。')
            if db.execute('SELECT COUNT(*) FROM support_requests').fetchone()[0] >= MAX_RECORDS:
                raise HTTPException(429, '反馈记录已满，请联系账号发放管理员。')
            ident, now = uuid.uuid4().hex, _now()
            db.execute('''INSERT INTO support_requests
                (id,owner_id,username,message,task_name,request_id,status,reply,created_at,updated_at)
                VALUES (?,?,?,?,?,?,'open','',?,?)''',
                (ident,actor['id'],actor['username'],payload['message'],payload['task_name'],payload['request_id'],now,now))
            return dict(db.execute('SELECT * FROM support_requests WHERE id=?',(ident,)).fetchone())

    def list(self, *, owner_id=None, status='', page=1, page_size=10):
        terms, args = [], []
        if owner_id is not None:
            terms.append('owner_id=?'); args.append(owner_id)
        if status:
            terms.append('status=?'); args.append(status)
        where = ' WHERE '+' AND '.join(terms) if terms else ''
        with self.connection() as db:
            db.execute('BEGIN')
            total = db.execute('SELECT COUNT(*) FROM support_requests'+where,args).fetchone()[0]
            pages = max(1, math.ceil(total/page_size)); page = min(page,pages)
            rows = db.execute('SELECT * FROM support_requests'+where+' ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?',
                              (*args,page_size,(page-1)*page_size)).fetchall()
            return {'items':[dict(row) for row in rows], 'total':total, 'page':page, 'pages':pages, 'page_size':page_size}

    def update(self, ident, actor, status, reply):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM support_requests WHERE id=?',(ident,)).fetchone():
                raise HTTPException(404,'反馈记录不存在。')
            db.execute('UPDATE support_requests SET status=?,reply=?,updated_at=?,replied_by=? WHERE id=?',
                       (status,reply,_now(),actor['id'],ident))
            return dict(db.execute('SELECT * FROM support_requests WHERE id=?',(ident,)).fetchone())


def _loopback(value):
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return value == 'localhost'


def _actor(request, *, admin=False):
    if tenancy.enabled():
        actor = getattr(request.state,'user',None)
        if not isinstance(actor,dict) or not actor.get('id') or not actor.get('enabled'):
            raise HTTPException(401,'请先登录。')
        if admin and actor.get('role') not in {'admin','super_admin'}:
            raise HTTPException(403,'此操作仅管理员可用。')
        return actor
    forwarded = request.headers.get('x-forwarded-for','')
    if (not request.client or not _loopback(request.client.host) or not _loopback(request.url.hostname or '')
            or (forwarded and not all(_loopback(part.strip()) for part in forwarded.split(',')))):
        raise HTTPException(403,'未启用账号时，反馈仅允许本机访问。')
    if request.method not in {'GET','HEAD'} and not same_origin(request):
        raise HTTPException(403,'请从本机工作台页面提交反馈。')
    return {'id':'local','username':'本机使用者','role':'admin','enabled':True}


def _secrets(settings, request):
    values = configured_secrets(settings)
    values.update(value for value in request.cookies.values() if value)
    session = getattr(request.state,'session',None) or {}
    if session.get('csrf_token'): values.add(session['csrf_token'])
    return values


def _clean(text, secrets, maximum):
    text = ''.join(char for char in text if not unicodedata.category(char).startswith('C') or char in '\n\t')
    text = re.sub(r'(?i)(?:ark_session|csrf_token|x-csrf-token|cookie)\s*[:=]\s*(?:"[^"]*"|\x27[^\x27]*\x27|[^\s,;]+)',
                  '[凭据已隐藏]',text)
    return safe_error(text,secrets,limit=maximum).strip()


def _public(item, *, admin=False):
    hidden = {'replied_by'} if admin else {'owner_id','username','replied_by'}
    return {key:value for key,value in item.items() if key not in hidden}


def public_config(settings):
    image_accept = ','.join(sorted(IMAGE_FORMATS))
    video_accept = ','.join(sorted(VIDEO_SUFFIXES))
    portrait_accept = ','.join(sorted(ext for ext, formats in IMAGE_FORMATS.items() if formats & limits.PORTRAIT_FORMATS))
    dimensions = (f'宽高均大于 {limits.PORTRAIT_MIN_DIMENSION}、小于 {limits.PORTRAIT_MAX_DIMENSION} 像素；'
                  f'宽高比大于 {limits.PORTRAIT_MIN_ASPECT}、小于 {limits.PORTRAIT_MAX_ASPECT}')
    return {
        'limits':{'message_max_chars':MESSAGE_MAX_CHARS,'task_name_max_chars':TASK_NAME_MAX_CHARS,
                  'request_id_max_chars':REQUEST_ID_MAX_CHARS,'page_size_max':PAGE_SIZE_MAX,
                  'open_requests_max':MAX_OPEN_PER_OWNER},
        'input_requirements':{
            'source_video':{'accept':video_accept,'text':f'原视频不超过 {settings.max_upload_mb} MB；支持 '+video_accept+'，文件必须可正常解码。'},
            'image':{'accept':image_accept,'text':f'图片不超过 {limits.IMAGE_MAX_BYTES//1024//1024} MB；支持 '+image_accept+f'；宽高均不超过 {MAX_DIMENSION} 像素、单帧像素总量不超过 {MAX_PIXELS}，文件必须可正常解码。'},
            'portrait_photo':{'accept':portrait_accept,'text':f'人物照片不超过 {limits.IMAGE_MAX_BYTES//1024//1024} MB，使用静态 '+ '/'.join(sorted(limits.PORTRAIT_FORMATS))+'；'+dimensions+'。'},
            'person_video':{'accept':','.join(sorted(limits.PERSON_VIDEO_EXTENSIONS)), 'text':f'人物视频不超过 {limits.PERSON_VIDEO_MAX_BYTES//1024//1024} MB，'+ '/'.join(ext[1:].upper() for ext in sorted(limits.PERSON_VIDEO_EXTENSIONS))+f'；单段 {limits.PERSON_VIDEO_MIN_SECONDS}–{limits.PERSON_VIDEO_MAX_SECONDS} 秒；帧率校验范围 {limits.PERSON_VIDEO_MIN_FPS}–{limits.PERSON_VIDEO_MAX_FPS} fps；'+dimensions+f'；像素总量 {limits.PERSON_VIDEO_MIN_PIXELS}–{limits.PERSON_VIDEO_MAX_PIXELS}。提交时还需满足所选模型的合计时长要求。'},
            'prompt':{'max_length':limits.PROMPT_MAX_CHARS,'text':f'用户提示词不超过 {limits.PROMPT_MAX_CHARS} 字符；参考图数量、生成时长及分辨率按当前模型选项校验。'}},
        'billing_notice':'本站当前未提供站内扣款功能，也未配置线下收费标准。第三方模型、解析及存储服务的实际费用以供应商规则和账单为准；如需确认账号使用或线下收费，请联系账号发放管理员。',
        'support':{'auth_enabled':tenancy.enabled(),'login_required':tenancy.enabled()}}


class _SupportRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def safe(request):
            try:
                if request.method in {'POST','PATCH'}:
                    chunks, size = [], 0
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > BODY_MAX_BYTES:
                            raise HTTPException(413,'反馈内容过长，请缩短后再提交。')
                        chunks.append(chunk)
                    request._body = b''.join(chunks)
                response = await handler(request)
            except RequestValidationError:
                response = JSONResponse({'detail':'反馈字段无效，请检查长度和错误编号。'},status_code=422)
            response.headers['Cache-Control'] = 'no-store'
            return response
        return safe


def get_router(settings_getter, templates):
    """settings_getter returns base settings; middleware owns authentication/CSRF."""
    router = APIRouter(route_class=_SupportRoute)
    def store(): return SupportStore(tenancy.config_root(settings_getter()))

    @router.get('/help')
    def help_page(request: Request):
        return templates.TemplateResponse(request=request,name='help.html',context={'support_admin':False,'auth_enabled':tenancy.enabled()})

    @router.get('/admin/support')
    def admin_page(request: Request):
        _actor(request,admin=True)
        return templates.TemplateResponse(request=request,name='help.html',context={'support_admin':True,'auth_enabled':tenancy.enabled()})

    @router.get('/api/support/config')
    def config(): return public_config(settings_getter())

    @router.post('/api/support/requests',status_code=201)
    def submit(request: Request, payload: Submit):
        actor = _actor(request)
        secrets = _secrets(settings_getter(),request)
        if payload.request_id and payload.request_id in secrets:
            raise HTTPException(422,'请填写错误编号，不要填写账号凭据。')
        values = {'message':_clean(payload.message,secrets,MESSAGE_MAX_CHARS),
                  'task_name':_clean(payload.task_name,secrets,TASK_NAME_MAX_CHARS),'request_id':payload.request_id}
        if not values['message']: raise HTTPException(422,'请填写问题描述。')
        return {'item':_public(store().create(actor,values))}

    @router.get('/api/support/requests')
    def own(request: Request,page:int=Query(1,ge=1),page_size:int=Query(10,ge=1,le=PAGE_SIZE_MAX)):
        actor = _actor(request)
        result = store().list(owner_id=actor['id'],page=page,page_size=page_size)
        result['items'] = [_public(item) for item in result['items']]
        return result

    @router.get('/api/admin/support/requests')
    def all_requests(request: Request,page:int=Query(1,ge=1),page_size:int=Query(10,ge=1,le=PAGE_SIZE_MAX),status:Literal['','open','resolved']=''):
        _actor(request,admin=True)
        result = store().list(status=status,page=page,page_size=page_size)
        result['items'] = [_public(item,admin=True) for item in result['items']]
        return result

    @router.patch('/api/admin/support/requests/{ident}')
    def reply(ident:str,request: Request,payload:Reply):
        actor = _actor(request,admin=True)
        text = _clean(payload.reply,_secrets(settings_getter(),request),MESSAGE_MAX_CHARS)
        return {'item':_public(store().update(ident,actor,payload.status,text),admin=True)}

    return router
