"""Offline template permissions match the explicitly supplied account fixture."""
from jinja2 import Environment, FileSystemLoader

LOCAL_ACCOUNT = {'auth_enabled': False, 'user': None, 'csrf_token': ''}
EDITOR_RULES_PATH = '/api/admin/prompt-templates/editor-rules.js'


def editor_visible(account):
    return not account['auth_enabled'] or (account.get('user') or {}).get('role') in {'admin', 'super_admin'}


def template_environment(root, account):
    env = Environment(loader=FileSystemLoader(root / 'app/templates'), autoescape=True)
    env.globals['prompt_editor_visible'] = lambda request: editor_visible(account() if callable(account) else account)
    return env


def serve_editor_rules(route, root, account):
    if not editor_visible(account):
        route.fulfill(status=403, json={'detail': '提示词由管理员管理。'})
        return
    route.fulfill(content_type='application/javascript',
                  body=(root / 'app/editor_assets/legacy-prompt-rules.js').read_text(encoding='utf-8'))
