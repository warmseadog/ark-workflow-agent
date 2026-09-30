"""Request readiness timings without private URLs, credentials or query strings."""
import logging
from time import perf_counter
import uuid

logger = logging.getLogger('ark.request_timing')


def install(app):
    @app.middleware('http')
    async def timing(request, call_next):
        ident = uuid.uuid4().hex
        started = perf_counter()
        response = await call_next(request)
        elapsed = (perf_counter() - started) * 1000
        response.headers['X-Request-ID'] = ident
        previous = response.headers.get('Server-Timing')
        value = f'app;dur={elapsed:.2f}'
        response.headers['Server-Timing'] = (previous + ', ' + value) if previous else value
        if elapsed >= 1000:
            route = getattr(request.scope.get('route'), 'path', '<unmatched>')
            logger.warning('slow_request id=%s method=%s route=%s status=%s ready_ms=%.2f',
                           ident, request.method, route, response.status_code, elapsed)
        return response
