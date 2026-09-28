"""Explicit, audited cross-user management; never reuse ordinary workspace APIs."""
import json
from fastapi import APIRouter, HTTPException, Query, Request
from .accounts import Accounts
from .production_store import ProductionStore
from . import tenancy


def disk_usage(root):
    # The legacy admin root also contains users and backups; never count those twice.
    total=0
    for name in ('assets','uploads','work','outputs','cache','playback','imports','portrait-thumbs'):
        directory=root/name
        if directory.is_dir():
            for path in directory.rglob('*'):
                if path.is_file() and not path.is_symlink():
                    try: total+=path.stat().st_size
                    except OSError: pass
    return total


def get_router(settings_getter,templates):
    router=APIRouter()
    def admin(request):
        user=getattr(request.state,'user',None)
        if not tenancy.enabled() or not user or user['role']!='admin':
            raise HTTPException(403,'此操作仅管理员可用。')
        return user

    @router.get('/admin/users')
    def users_page(request:Request):
        admin(request)
        return templates.TemplateResponse(request=request,name='users.html',context={})

    @router.get('/api/admin/tasks')
    def tasks(request:Request,user_id:str='',status:str='',page:int=Query(1,ge=1),page_size:int=Query(100,ge=1,le=200)):
        actor=admin(request)
        if status and status not in {'queued','running','succeeded','failed','needs_attention','cancelled'}:
            raise HTTPException(422,'任务状态无效。')
        accounts=Accounts(settings_getter().storage_dir)
        tenants=tenancy.tenant_settings(settings_getter(),include_disabled=True)
        if user_id:
            tenants=[item for item in tenants if item[0]['id']==user_id]
            if not tenants:raise HTTPException(404,'用户不存在。')
        items=[]
        stats={'submitted':0,'succeeded':0,'failed':0,'generated_seconds':0,'duration_unknown':0,'storage_bytes':0}
        for user,effective in tenants:
            store=ProductionStore(effective.storage_dir)
            with store.connection() as db:
                rows=db.execute('SELECT id,status,stage,message,progress,snapshot,created_at,updated_at FROM production_runs ORDER BY created_at DESC').fetchall()
                metadata={row['id']:json.loads(row['metadata'] or '{}') for row in db.execute('SELECT id,metadata FROM production_playbacks')}
            stats['submitted']+=len(rows)
            stats['storage_bytes']+=disk_usage(effective.storage_dir)
            for row in rows:
                if row['status'] in ('succeeded','failed'):stats[row['status']]+=1
                if row['status']=='succeeded':
                    duration=metadata.get(row['id'],{}).get('duration')
                    if isinstance(duration,(int,float)) and duration>0:stats['generated_seconds']+=duration
                    else:stats['duration_unknown']+=1
                if status and row['status']!=status:continue
                snapshot=json.loads(row['snapshot'])
                items.append({key:row[key] for key in ('id','status','stage','message','progress','created_at','updated_at')} |
                    {'user_id':user['id'],'username':user['username'],'name':store.run_name(row['id'],snapshot.get('name','视频')),
                     'model':snapshot.get('model',{}).get('model',''),'duration':metadata.get(row['id'],{}).get('duration')})
        items.sort(key=lambda item:(item['created_at'],item['id']),reverse=True)
        accounts.audit(actor['id'],'view_user_tasks',user_id or 'all')
        start=(page-1)*page_size
        return {'items':items[start:start+page_size],'total':len(items),'page':page,'page_size':page_size,'stats':stats}

    @router.get('/api/admin/tasks/{user_id}/{run_id}')
    def task_detail(request:Request,user_id:str,run_id:str):
        actor=admin(request)
        accounts=Accounts(settings_getter().storage_dir)
        user=accounts.get_user(user_id)
        effective=tenancy.user_settings(settings_getter(),user)
        store=ProductionStore(effective.storage_dir)
        try:
            store.require_visible(run_id)
            result=store.get_run(run_id)
        except LookupError:raise HTTPException(404,'找不到任务。') from None
        accounts.audit(actor['id'],'view_user_task',user_id+':'+run_id)
        return {'user_id':user_id,'username':user['username'],'task':result}

    return router
