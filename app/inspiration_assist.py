"""One bounded text-only call. No draft reads, video jobs or camera planning."""
import asyncio
from contextlib import contextmanager
import hashlib
import logging
import re
import time
import uuid

import httpx
from fastapi import HTTPException

from . import inspiration_settings, prompt_visibility
from .queue_admission import _lease
from .secure_transport import validate_endpoint
from .tenancy import config_root

logger = logging.getLogger(__name__)


def normalize_input(payload):
    if not isinstance(payload, dict) or set(payload) != {'inspiration'}:
        raise ValueError('灵感辅助请求格式不正确。')
    text = payload['inspiration']
    if not isinstance(text, str) or len(text) > 2000 or '\x00' in text:
        raise ValueError('拍摄灵感最多 2000 字。')
    return text.strip()


@contextmanager
def admission(settings):
    """OS leases enforce one request per actor and four per site across workers."""
    folder = config_root(settings) / 'private' / 'inspiration-admission'
    folder.mkdir(parents=True, exist_ok=True)
    actor = (prompt_visibility.actor(settings) or {}).get('id') or settings.user_id or 'local'
    user = _lease(folder / ('user-' + hashlib.sha256(actor.encode()).hexdigest() + '.lock'))
    if user is None:
        raise HTTPException(429, '正在生成灵感，请稍候再试。', headers={'Retry-After': '2'})
    slot = None
    try:
        for index in range(4):
            slot = _lease(folder / f'slot-{index}.lock')
            if slot is not None:
                break
        if slot is None:
            raise HTTPException(429, '灵感辅助繁忙，请稍后重试。', headers={'Retry-After': '2'})
        yield
    finally:
        if slot is not None:
            slot.close()
        user.close()


def output_text(data):
    try:
        choice = data['choices'][0]
        text = choice['message']['content']
        if choice.get('finish_reason') != 'stop' or not isinstance(text, str):
            raise ValueError()
        text = text.strip()
        if (not text or len(text) > 500 or '\x00' in text or '```' in text
                or text.startswith(('{', '[', '#', '<')) or re.search(r'(?m)^\s*(?:\d+[.、)]|[-*])\s', text)):
            raise ValueError()
        return text
    except (ValueError, KeyError, IndexError, TypeError):
        raise HTTPException(502, '未获取到有效灵感，请重试。原内容已保留。') from None


async def _complete(config, body):
    # The outer wait_for also bounds DNS, pool wait, response reads and client teardown.
    async with httpx.AsyncClient(timeout=httpx.Timeout(config.timeout_seconds, connect=3),
                                 follow_redirects=False) as client:
        response = await client.post(validate_endpoint(config.base_url) + '/chat/completions',
                                     headers={'Authorization': 'Bearer ' + config.api_key}, json=body)
        if response.status_code != 200:
            raise HTTPException(502, '暂时未能生成灵感，请稍后重试。')
        try:
            return output_text(response.json())
        except ValueError:
            raise HTTPException(502, '未获取到有效灵感，请重试。原内容已保留。') from None


async def generate(settings, payload):
    try:
        text = normalize_input(payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    try:
        config = inspiration_settings.load_config(settings).resolved(settings)
        skill = inspiration_settings.skill_text(inspiration_settings.prompt_mode(settings))
    except (ValueError, OSError):
        raise HTTPException(503, 'AI 灵感辅助暂不可用，可直接填写拍摄想法。') from None
    if config.problem():
        raise HTTPException(503, config.problem())
    ident, started = uuid.uuid4().hex, time.monotonic()
    version = hashlib.sha256(skill.encode()).hexdigest()[:12]
    body = {'model': config.model, 'max_tokens': config.max_tokens,
            'messages': [{'role': 'system', 'content': skill}, {'role': 'user', 'content':
                '请围绕以下用户灵感完善拍摄安排：\n' + text if text else '请生成一条人物穿搭展示的拍摄灵感。'}]}
    if config.model.startswith('doubao-seed-'):
        body['thinking'] = {'type': 'disabled'}
    outcome = 'error'
    try:
        with admission(settings):
            result = await asyncio.wait_for(_complete(config, body), timeout=config.timeout_seconds)
        outcome = 'ok'
        return {'inspiration': result, 'request_id': ident}
    except (asyncio.TimeoutError, httpx.TimeoutException):
        outcome = 'timeout'
        raise HTTPException(504, '灵感生成超时，请重试。原内容已保留。') from None
    except httpx.HTTPError:
        raise HTTPException(502, '暂时未能生成灵感，请稍后重试。') from None
    except OSError:
        raise HTTPException(503, 'AI 灵感辅助暂不可用，可直接填写拍摄想法。') from None
    finally:
        logger.info('inspiration request=%s model=%s skill=%s elapsed_ms=%d outcome=%s',
                    ident, config.model, version, (time.monotonic() - started) * 1000, outcome)
