"""Project server-owned prompt data according to the real authenticated actor."""
from . import tenancy

PROMPT_FIELDS = {'prompt', 'prompt_template_id', 'prompt_rule_version', 'continuation_prompt',
                 'final_prompt', 'resolved_prompt', 'system_prompt', 'plan', 'variation'}


def actor(settings=None):
    user = getattr(tenancy, '_request_user', None)
    current = user.get() if user is not None else None
    if current is not None or settings is None or not tenancy.enabled():
        return current
    from .accounts import Accounts
    return Accounts(tenancy.config_root(settings)).get_user(settings.user_id) if settings.user_id else None


def can_edit(settings=None):
    return not tenancy.enabled() or (actor(settings) or {}).get('role') in {'admin', 'super_admin'}


def require_editor(settings=None):
    from fastapi import HTTPException
    if not can_edit(settings):
        raise HTTPException(403, '提示词由管理员管理。')


def project(value, settings):
    """Copy public values; never strip the stored immutable task snapshot."""
    visible = can_edit(settings)
    who = actor(settings)
    delegated = bool(who and settings.user_id and who['id'] != settings.user_id)
    def visit(item):
        if isinstance(item, dict):
            result = {key:visit(child) for key,child in item.items()
                    if visible or key not in PROMPT_FIELDS}
            return result
        if isinstance(item, list):
            return [visit(child) for child in item]
        if delegated and isinstance(item, str):
            for prefix in ('/api/production/', '/api/portrait/', '/api/previews/'):
                if item.startswith(prefix):
                    return '/api/admin/delegated/'+settings.user_id+item[4:]
        return item
    return visit(value)
