"""Private prompt overrides and attempt-local rendering; never execute template code."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from threading import RLock
import hashlib
import json
import os
import string
import tempfile

_values=ContextVar('prompt_values',default={})
_trace=ContextVar('prompt_trace',default=None)
lock=RLock()


def catalog():
    return json.loads(Path(__file__).with_name('prompt_catalog.json').read_text(encoding='utf-8'))


def path(settings):
    from .tenancy import config_root
    return config_root(settings)/'private/prompt-overrides.json'


def load(settings):
    target=path(settings)
    if not target.exists():return {}
    values=json.loads(target.read_text(encoding='utf-8'))
    validate(values)
    return values


def fields(value):
    result=[]
    for _,name,spec,conversion in string.Formatter().parse(value):
        if name is not None:
            if not name.isidentifier() or spec or conversion:
                raise ValueError('占位符仅支持 {名称}，不能加入属性、格式或代码。')
            result.append(name)
    return set(result)


def validate(values):
    definitions={item['id']:item for item in catalog()}
    if not isinstance(values,dict) or set(values)-set(definitions):raise ValueError('提示词条目不正确。')
    for key,value in values.items():
        if not isinstance(value,str) or not value.strip() or len(value)>30000 or '\x00' in value:
            raise ValueError('提示词需为 1–30000 字的纯文本。')
        if definitions[key]['variables'] and fields(value)!=set(definitions[key]['variables']):raise ValueError('请保留本段原有的全部占位符。')


def save(settings,values):
    validate(values)
    target=path(settings);target.parent.mkdir(parents=True,exist_ok=True)
    fd,temporary=tempfile.mkstemp(dir=target.parent,prefix='.prompt-',suffix='.tmp')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(values,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,target)
    finally:Path(temporary).unlink(missing_ok=True)


@contextmanager
def scope(values,trace=None):
    token=_values.set(values);record=_trace.set(trace)
    try:yield
    finally:_trace.reset(record);_values.reset(token)


def text(key,default,**values):
    template=_values.get().get(key,default)
    # Literal braces in source defaults are never interpreted without variables.
    result=template.format_map(values) if values else template
    trace=_trace.get()
    if trace is not None:trace.append({'id':key,'content':result})
    return result


def frozen_run(function):
    @wraps(function)
    def call(settings,store,run,*args,**kwargs):
        with scope(run['private'].get('prompt_overrides',{})):
            return function(settings,store,run,*args,**kwargs)
    return call
