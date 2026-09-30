"""Explicit, audited cross-user management; never reuse ordinary workspace APIs."""
from fastapi import APIRouter, HTTPException, Query, Request
from .accounts import Accounts
from .production_store import ProductionStore
from . import tenancy


def disk_usage(root):
    # The legacy admin root also contains users and backups; never count those twice.
    total=0
    for name in ('assets','uploads','work','outputs','cache','playback','imports','portrait-thumbs','asset-thumbs'):
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
    def tasks(request:Request,user_id:str='',status:str='',page:int=Query(1,ge=1),page_size:int=Query(10,ge=1,le=200)):
        actor=admin(request)
        if status and status not in {'queued','running','succeeded','failed','needs_attention','cancelled'}:
            raise HTTPException(422,'任务状态无效。')
        accounts=Accounts(settings_getter().storage_dir)
        tenants=tenancy.tenant_settings(settings_getter(),include_disabled=True)
        if user_id:
            tenants=[item for item in tenants if item[0]['id']==user_id]
            if not tenants:raise HTTPException(404,'用户不存在。')
        items=[]; stores={}; total=0
        stats={'submitted':0,'succeeded':0,'failed':0,'generated_seconds':0,'duration_unknown':0,'storage_bytes':0}
        for user,effective in tenants:
            store=ProductionStore(effective.storage_dir)
            stores[user['id']]=store
            with store.connection() as db:
                aggregate=db.execute("""SELECT COUNT(*) AS submitted,
                    COALESCE(SUM(r.status='succeeded'),0) AS succeeded,
                    COALESCE(SUM(r.status='failed'),0) AS failed,
                    COALESCE(SUM(CASE WHEN r.status='succeeded' AND
                        json_type(p.metadata,'$.duration') IN ('integer','real') AND json_extract(p.metadata,'$.duration')>0
                        THEN json_extract(p.metadata,'$.duration') ELSE 0 END),0) AS generated_seconds,
                    COALESCE(SUM(CASE WHEN r.status='succeeded' AND COALESCE(
                        json_type(p.metadata,'$.duration') IN ('integer','real') AND json_extract(p.metadata,'$.duration')>0,0)=0
                        THEN 1 ELSE 0 END),0) AS duration_unknown
                    FROM production_runs r LEFT JOIN production_playbacks p ON p.id=r.id""").fetchone()
                for key in aggregate.keys(): stats[key]+=aggregate[key]
                total+=db.execute('SELECT COUNT(*) FROM production_runs'+(' WHERE status=?' if status else ''),
                                  (status,) if status else ()).fetchone()[0]
            stats['storage_bytes']+=disk_usage(effective.storage_dir)
        pages=max(1,(total+page_size-1)//page_size); page=min(page,pages)
        for user,_ in tenants:
            store=stores[user['id']]
            with store.connection() as db:
                # Each tenant contributes only its first N candidates to the
                # global merge. Timings are hydrated only for the final page.
                rows=db.execute("""SELECT r.id,r.status,r.stage,r.message,r.progress,r.created_at,r.updated_at,
                    COALESCE(n.name,json_extract(r.snapshot,'$.name'),'视频') AS name,
                    json_extract(r.snapshot,'$.model.model') AS model,
                    json_extract(p.metadata,'$.duration') AS duration
                    FROM production_runs r LEFT JOIN production_run_names n ON n.id=r.id
                    LEFT JOIN production_playbacks p ON p.id=r.id """+
                    ('WHERE r.status=? ' if status else '')+'ORDER BY r.created_at DESC,r.id DESC LIMIT ?',
                    ((status,) if status else ())+(page*page_size,)).fetchall()
            for row in rows:
                items.append(dict(row) | {'user_id':user['id'],'username':user['username']})
        items.sort(key=lambda item:(item['created_at'],item['id']),reverse=True)
        accounts.audit(actor['id'],'view_user_tasks',user_id or 'all')
        start=(page-1)*page_size; items=items[start:start+page_size]
        for ident,store in stores.items():
            selected=[item for item in items if item['user_id']==ident]
            if selected:
                timings=store.run_timings(item['id'] for item in selected)
                for item in selected:item['timing']=timings[item['id']]
        return {'items':items,'total':total,'page':page,'pages':pages,'page_size':page_size,'stats':stats}

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
