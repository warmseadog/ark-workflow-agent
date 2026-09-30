"""Optional Nginx file delivery. Call only after record-level authorization."""
import os
from pathlib import Path
from urllib.parse import quote

from starlette.responses import FileResponse, Response
from .tenancy import config_root


def media_file_response(settings, path, *, media_type=None, filename=None, headers=None):
    root = config_root(settings).resolve()
    resolved = Path(path).resolve()
    # Prevent a corrupt record or symlink from handing Nginx an external path.
    # Record ownership must already have been checked by the API handler.
    relative = resolved.relative_to(root)
    private_headers = {**(headers or {}), 'Cache-Control':'private, no-store', 'X-Content-Type-Options':'nosniff'}
    direct = FileResponse(resolved, media_type=media_type, filename=filename, headers=private_headers)
    if os.getenv('APP_ACCEL_MEDIA','').strip().lower() not in {'1','true','yes','on'}:
        return direct
    if not resolved.is_file():
        raise FileNotFoundError('媒体文件不存在。')
    result = Response(media_type=media_type, headers=dict(direct.headers))
    if 'content-length' in result.headers:
        del result.headers['content-length']
    result.headers['X-Accel-Redirect'] = '/_ark_media_internal/' + quote(relative.as_posix(), safe='/')
    return result
