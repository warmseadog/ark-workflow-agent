"""Auth HTTP endpoints; main owns authentication/CSRF/permission middleware.

Middleware sets request.state.user to a public user and request.state.session
to Accounts.authenticate's result. settings_getter must return BASE settings.
"""
from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool, StrictInt

from .accounts import Accounts, AccountError, SESSION_SECONDS


COOKIE_NAME = 'ark_session'


def auth_enabled() -> bool:
    from .tenancy import enabled
    return enabled()


def _secure_cookie() -> bool:
    # Only an explicit false opts out; malformed configuration stays secure.
    return os.getenv('APP_COOKIE_SECURE', 'true').strip().lower() not in {'0', 'false', 'no', 'off'}


def _origin(value: str) -> tuple | None:
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.path not in {'', '/'} or '?' in value or '#' in value
                or any(char.isspace() for char in value)):
            return None
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == 'https' else 80)
        return parsed.scheme, parsed.hostname.lower(), port
    except ValueError:
        return None


def _same_origin(request: Request) -> None:
    supplied = request.headers.getlist('origin')
    expected = _origin(os.getenv('APP_PUBLIC_ORIGIN') or str(request.base_url))
    if len(supplied) != 1 or expected is None or _origin(supplied[0]) != expected:
        raise HTTPException(403, '请从本工作台页面发起登录请求。')


def _clear_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path='/', secure=_secure_cookie(), httponly=True, samesite='lax')


class _AuthRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request: Request):
            try:
                response = await handler(request)
            except AccountError as exc:
                response = JSONResponse({'detail': exc.detail}, status_code=exc.status_code)
            except RequestValidationError:
                # FastAPI's default validation response echoes rejected inputs.
                # Never include credential-bearing request bodies in a response.
                response = JSONResponse({'detail': '请求字段无效，请检查填写内容。'}, status_code=422)
            response.headers['Cache-Control'] = 'no-store'
            return response

        return safe_handler


class _Input(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class _Login(_Input):
    username: str = Field(min_length=1, max_length=256)
    password: SecretStr = Field(max_length=1024)


class _PasswordChange(_Input):
    current_password: SecretStr = Field(max_length=1024)
    new_password: SecretStr = Field(min_length=6, max_length=1024)


class _CreateUser(_Login):
    password: SecretStr = Field(min_length=6, max_length=1024)
    role: Literal['admin', 'user'] = 'user'
    max_concurrent: StrictInt = Field(default=1, ge=1, le=10000)
    max_queued: StrictInt = Field(default=10, ge=0, le=10000)


class _UpdateUser(_Input):
    # PATCH passes only explicitly supplied fields; omission preserves the role.
    role: Literal['admin', 'user'] = 'user'
    enabled: StrictBool | None = None
    max_concurrent: StrictInt | None = Field(default=None, ge=1, le=10000)
    max_queued: StrictInt | None = Field(default=None, ge=0, le=10000)


class _ResetPassword(_Input):
    password: SecretStr = Field(min_length=6, max_length=1024)


def get_router(settings_getter, templates) -> APIRouter:
    router = APIRouter(route_class=_AuthRoute)

    @lru_cache(maxsize=16)
    def store_for_root(root: Path) -> Accounts:
        return Accounts(root)

    def accounts() -> Accounts:
        return store_for_root(Path(settings_getter().storage_dir).resolve())

    def require_enabled():
        if not auth_enabled():
            raise AccountError(403, '当前未启用账号认证。')

    def current_user(request: Request, *, admin=False) -> dict:
        require_enabled()
        user = getattr(request.state, 'user', None)
        if not isinstance(user, dict) or not user.get('id'):
            raise AccountError(401, '请先登录。')
        try:
            user = accounts().get_user(user['id'])
        except AccountError:
            raise AccountError(401, '请先登录。') from None
        if not user['enabled']:
            raise AccountError(401, '请先登录。')
        if admin and user['role'] != 'admin':
            raise AccountError(403, '需要管理员权限。')
        return user

    @router.get('/login')
    def login_page(request: Request):
        return templates.TemplateResponse(request=request, name='login.html', context={'auth_enabled': auth_enabled()})

    @router.get('/account/password')
    def password_page(request: Request):
        return templates.TemplateResponse(request=request, name='password.html',
                                          context={'user': getattr(request.state, 'user', None)})

    @router.post('/api/auth/login')
    def login(request: Request, payload: _Login, response: Response):
        require_enabled()
        _same_origin(request)
        session = accounts().login(payload.username, payload.password.get_secret_value(),
                                   request.client.host if request.client else 'unknown')
        # Replacing a browser session also invalidates its previous cookie.
        accounts().logout(request.cookies.get(COOKIE_NAME))
        response.set_cookie(COOKIE_NAME, session['token'], max_age=SESSION_SECONDS,
                            path='/', secure=_secure_cookie(), httponly=True, samesite='lax')
        return {'user': session['user'], 'csrf_token': session['csrf_token']}

    @router.get('/api/auth/me')
    def me(request: Request):
        if not auth_enabled():
            return {'user': None, 'csrf_token': '', 'auth_enabled': False}
        user = current_user(request)
        session = getattr(request.state, 'session', None)
        if not isinstance(session, dict) or not session.get('csrf_token'):
            raise AccountError(401, '请先登录。')
        return {'user': user, 'csrf_token': session['csrf_token'], 'auth_enabled': True}

    @router.post('/api/auth/password')
    def change_password(request: Request, payload: _PasswordChange, response: Response):
        user = current_user(request)
        accounts().change_password(user['id'], payload.current_password.get_secret_value(),
                                   payload.new_password.get_secret_value())
        _clear_cookie(response)
        return {'needs_login': True}

    @router.post('/api/auth/logout', status_code=204)
    def logout(request: Request):
        accounts().logout(request.cookies.get(COOKIE_NAME))
        response = Response(status_code=204)
        _clear_cookie(response)
        return response

    @router.get('/api/admin/users')
    def list_users(request: Request):
        current_user(request, admin=True)
        return {'items': accounts().list_users()}

    @router.post('/api/admin/users', status_code=201)
    def create_user(request: Request, payload: _CreateUser):
        actor = current_user(request, admin=True)
        return {'user': accounts().create_user(payload.username, payload.password.get_secret_value(),
                                               actor['id'], payload.max_concurrent, payload.max_queued, role=payload.role)}

    @router.patch('/api/admin/users/{user_id}')
    def update_user(user_id: str, request: Request, payload: _UpdateUser):
        actor = current_user(request, admin=True)
        return {'user': accounts().update_user(user_id, actor['id'],
                                               **payload.model_dump(exclude_none=True, exclude_unset=True))}

    @router.post('/api/admin/users/{user_id}/reset-password')
    def reset_password(user_id: str, request: Request, payload: _ResetPassword):
        actor = current_user(request, admin=True)
        return {'user': accounts().reset_password(user_id, payload.password.get_secret_value(), actor['id'])}

    @router.get('/api/admin/audit')
    def list_audit(request: Request, limit: int | None = Query(default=None, ge=1, le=1000),
                   page: int = Query(default=1, ge=1), page_size: int = Query(default=10, ge=1, le=200),
                   scope: str = Query(default='important', pattern='^(important|all)$'), user_id: str = ''):
        current_user(request, admin=True)
        if user_id:
            accounts().get_user(user_id)
        if limit is not None and not user_id:  # Preserve unfiltered legacy callers.
            return {'items': accounts().list_audit(limit)}
        return accounts().audit_page(page, page_size, scope, user_id)

    return router
