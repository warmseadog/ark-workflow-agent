"""Durable one-time Ark verification sessions; callbacks never grant authorization."""
from contextlib import contextmanager
from io import BytesIO
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from urllib.parse import urlsplit
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from . import portrait_service as service

_lock = threading.RLock()
_NO_STORE = {'Cache-Control':'no-store', 'Referrer-Policy':'no-referrer', 'X-Content-Type-Options':'nosniff'}

def public_base():
    value=os.environ.get('PORTRAIT_PUBLIC_BASE_URL','').strip().rstrip('/')
    try:
        p=urlsplit(value)
        if (p.scheme!='https' or not p.hostname or p.username or p.password or p.path or p.query or p.fragment
                or any(ord(c)<33 for c in value)): return ''
        _=p.port
    except ValueError: return ''
    return value

class Sessions:
    def __init__(self, settings):
        self.settings=settings
        self.path=settings.storage_dir/'private'/'portrait-sessions.db'
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, request_key TEXT UNIQUE, account TEXT, state TEXT UNIQUE, data TEXT)')
        if os.name!='nt': self.path.chmod(0o600)

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=45); db.row_factory=sqlite3.Row
        try:
            with db: yield db
        finally: db.close()

    def config(self):
        cfg=service.load_config(self.settings)
        if not cfg.ready: raise HTTPException(409,cfg.problem())
        return cfg

    def get(self, ident=None, state=None):
        field,value=('id',ident) if ident is not None else ('state',state)
        with self.db() as db: row=db.execute('SELECT * FROM sessions WHERE '+field+'=?',(value,)).fetchone()
        if row is None: raise HTTPException(404,'认证会话不存在，请重新获取二维码。')
        data=json.loads(row['data']); data.update(id=row['id'],account=row['account'],state=row['state'])
        if data['account']!=service.fingerprint(self.config()): raise HTTPException(409,'认证配置已变更，请重新获取二维码。')
        if data['status'] in {'pending','creating'} and data['expires_at']<=time.time():
            data.update(status='expired',token='',url='',message='二维码已过期，请重新获取。'); self.save(data)
        return data

    def save(self, data):
        with self.db() as db: db.execute('UPDATE sessions SET data=? WHERE id=?',(json.dumps(data,ensure_ascii=False),data['id']))

    @staticmethod
    def public(data):
        return {k:data.get(k) for k in ('id','status','expires_at','group_id','message')} | {'qr_url':'/api/portrait/sessions/'+data['id']+'/qr'}

    def create(self, key):
        base=public_base()
        if not base: raise HTTPException(409,'尚未配置公网认证返回地址，请使用服务器工作台。')
        if not isinstance(key,str) or not re.fullmatch(r'[a-f0-9]{32}',key): raise HTTPException(422,'认证请求标识无效，请重新打开弹窗。')
        with _lock:
            cfg=self.config(); account=service.fingerprint(cfg)
            request_key=hashlib.sha256((account+key).encode()).hexdigest()
            with self.db() as db:
                existing=db.execute('SELECT id FROM sessions WHERE request_key=?',(request_key,)).fetchone()
            if existing: return self.public(self.get(existing['id']))
            stamp=time.time(); ident=uuid.uuid4().hex; state=secrets.token_hex(32)
            data={'id':ident,'account':account,'state':state,'status':'creating','expires_at':stamp+1800,
                  'message':'正在创建官方认证会话…','token':'','url':'','group_id':None,'callback_seen':False,'checked_at':0,'base':base}
            with self.db() as db:
                # Bound outstanding sessions and remove expired credentials even if no browser is polling.
                for row in db.execute('SELECT id,data FROM sessions').fetchall():
                    old=json.loads(row['data'])
                    if old['expires_at']<stamp and old.get('token'):
                        old.update(token='',url='')
                        if old['status'] in {'pending','creating'}: old['status']='expired'
                        db.execute('UPDATE sessions SET data=? WHERE id=?',(json.dumps(old),row['id']))
                recent=db.execute('SELECT data FROM sessions WHERE account=?',(account,)).fetchall()
                if sum(json.loads(x['data'])['expires_at']>stamp and json.loads(x['data'])['status'] in {'creating','pending'} for x in recent)>=10:
                    raise HTTPException(429,'待认证二维码过多，请完成已有认证或稍后再试。')
                db.execute('INSERT INTO sessions VALUES (?,?,?,?,?)',(ident,request_key,account,state,json.dumps(data)))
            try:
                created=service.ArkPortraitClient(cfg).create_session(base+'/auth/portrait/return/'+state)
                data.update(created,status='pending',message='请本人使用手机扫码，在官方页面完成认证。')
            except service.PortraitError as error:
                data.update(status='failed',message=str(error)); self.save(data)
                raise HTTPException(502,str(error)) from None
            self.save(data)
            return self.public(data)

    def status(self, ident):
        with _lock:
            data=self.get(ident)
            if data['status']=='creating' and time.time()>data['expires_at']-1740:
                data.update(status='failed',message='认证请求未能完成，请重新获取二维码。'); self.save(data)
            if data['status']=='pending' and data['callback_seen'] and time.time()-data['checked_at']>=5:
                data['checked_at']=time.time()
                try:
                    group=service.ArkPortraitClient(self.config()).validation_result(data['token'])
                    if group:
                        data.update(status='verified',group_id=group,token='',url='',message='官方真人认证已通过。人物图片仍需通过官方素材校验后使用。')
                    else: data['message']='已收到手机返回，正在等待官方确认…'
                except service.PortraitError as error: data['message']=str(error)
                self.save(data)
            result=self.public(data)
            if data['status']=='verified':
                from .portrait_library import PortraitLibrary
                result['person_id']=PortraitLibrary(self.settings).add_person(data['group_id'])['id']
            return result

    def callback(self, state, token, result):
        with _lock:
            try: data=self.get(state=state)
            except HTTPException: raise HTTPException(400,'认证链接无效或已过期，请返回电脑重新获取二维码。') from None
            if data['status']!='pending' or not token or not hmac.compare_digest(data['token'].encode(),token.encode()):
                raise HTTPException(400,'认证链接无效或已过期，请返回电脑重新获取二维码。')
            if result=='10000': data.update(callback_seen=True,message='手机认证已返回，正在核实官方结果…')
            else: data.update(status='failed',token='',url='',message='本次手机认证未完成或未通过，请重新获取二维码。')
            self.save(data)

def routers(settings_getter, local_guard):
    secured=APIRouter(prefix='/api/portrait/sessions',dependencies=[Depends(local_guard)])
    public=APIRouter(prefix='/auth/portrait')
    def store(): return Sessions(settings_getter())
    @secured.post('')
    def create(payload:dict, response:Response):
        response.headers.update(_NO_STORE); return store().create(payload.get('request_id'))
    @secured.get('/{ident}')
    def status(ident:str, response:Response):
        response.headers.update(_NO_STORE); return store().status(ident)
    @secured.delete('/{ident}')
    def cancel(ident:str):
        with _lock:
            session_store=store(); data=session_store.get(ident)
            if data['status'] in {'pending','creating'}:
                data.update(status='cancelled',token='',url='',message='已重新获取二维码，旧链接失效。')
                session_store.save(data)
            return session_store.public(data)
    @secured.get('/{ident}/qr')
    def qr(ident:str):
        data=store().get(ident)
        if data['status']!='pending': raise HTTPException(410,'二维码不可用，请重新获取。')
        import qrcode
        from qrcode.image.svg import SvgPathImage
        output=BytesIO(); qrcode.make(data['base']+'/auth/portrait/scan/'+data['state'],image_factory=SvgPathImage,box_size=6,border=4).save(output)
        return Response(output.getvalue(),media_type='image/svg+xml',headers=_NO_STORE)
    @public.get('/scan/{state}')
    def scan(state:str):
        data=store().get(state=state)
        if data['status']!='pending': raise HTTPException(410,'二维码已失效，请返回电脑重新获取。')
        return RedirectResponse(data['url'],status_code=302,headers=_NO_STORE)
    @public.get('/return/{state}')
    def callback(state:str, request:Request):
        store().callback(state,request.query_params.get('bytedToken',''),request.query_params.get('resultCode',''))
        return RedirectResponse('/auth/portrait/done',status_code=303,headers=_NO_STORE)
    @public.get('/done')
    def done():
        return HTMLResponse('<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>认证结果已接收</title><body style="font-family:system-ui;padding:32px;line-height:1.8"><h2>手机操作已返回</h2><p>请返回电脑工作台查看官方确认结果。此页面不代表认证已通过。</p></body></html>',headers=_NO_STORE)
    return secured,public
