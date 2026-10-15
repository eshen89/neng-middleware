"""Payload-free, opt-in local timing; correlation is never authorization.

Context variables follow async calls and FastAPI's worker threads. Phase totals are
inclusive; self time subtracts the union of nested intervals on the same local clock.
No SQL, parameters, URLs, identity, or exception text enters a record.
"""
from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import perf_counter
from uuid import UUID, uuid4

HEADER = b"x-neng-request-id"
PHASES = frozenset({
    "backend_app", "auth_total", "auth_lookup", "auth_fetch", "auth_verify",
    "sdk_headers", "sdk_stream", "db_checkout", "db_connect", "db_setup", "db_sql",
    "db_commit", "db_rollback",
})
_current = ContextVar("neng_timing", default=None)
_frame = ContextVar("neng_timing_frame", default=None)


def canonical_id(value):
    if not isinstance(value, str) or len(value) != 36:
        return None
    try:
        parsed = UUID(value)
        return value if parsed.version == 4 and str(parsed) == value else None
    except ValueError:
        return None


@dataclass
class _Frame:
    children: list = field(default_factory=list)


def _union_duration(intervals):
    end = float('-inf')
    duration = 0.0
    for start, stop in sorted(intervals):
        duration += max(0.0, stop - max(start, end))
        end = max(end, stop)
    return duration


class RequestTiming:
    def __init__(self, scope):
        values = [v.decode('latin-1') for k, v in scope.get('headers', []) if k.lower() == HEADER]
        valid = canonical_id(values[0]) if len(values) == 1 else None
        self.trace_id = valid or str(uuid4())
        self.span_id = str(uuid4())
        self.id_source = 'upstream-asserted' if valid else 'generated'
        self.enabled = os.getenv('NENG_TRACE_ENABLED') == 'true'
        self.started = perf_counter()
        self.body_finished_at = None
        self.phases = {}
        self.lock = threading.Lock()
        self.native_id = None
        if os.getenv('AWS_LAMBDA_FUNCTION_NAME'):
            contexts = [v for k, v in scope.get('headers', [])
                        if k.lower() == b'x-amzn-lambda-context']
            if len(contexts) == 1 and len(contexts[0]) <= 8192:
                try:
                    self.native_id = canonical_id(json.loads(contexts[0]).get('request_id'))
                except (ValueError, AttributeError, UnicodeDecodeError):
                    pass


@contextmanager
def phase(name):
    if name not in PHASES:
        raise ValueError('Unknown timing phase')
    trace = _current.get()
    if trace is None or not trace.enabled:
        yield
        return
    parent = _frame.get()
    frame = _Frame()
    token = _frame.set(frame)
    start = perf_counter()
    failed = False
    try:
        yield
    except BaseException:
        failed = True
        raise
    finally:
        stop = perf_counter()
        _frame.reset(token)
        with trace.lock:
            if trace.body_finished_at is not None:
                stop = min(stop, trace.body_finished_at)
            if stop >= start:
                total = stop - start
                children = [(max(start, a), min(stop, b)) for a, b in frame.children
                            if a < stop and b > start]
                own = max(0.0, total - _union_duration(children))
                item = trace.phases.setdefault(name, {'total_ms': 0.0, 'self_ms': 0.0,
                                                      'count': 0, 'errors': 0})
                item['total_ms'] += total * 1000
                item['self_ms'] += own * 1000
                item['count'] += 1
                item['errors'] += int(failed)
                if parent is not None:
                    parent.children.append((start, stop))


class TimingMiddleware:
    """Wrap outside FastAPI's error middleware so generated 500s remain correlated."""
    def __init__(self, app, *, service, entry_point):
        if service not in {'neng-agent', 'neng-user', 'neng-devsai', 'neng-gateway'}:
            raise ValueError('Unknown service')
        if entry_point not in {'public-http', 'internal-http'}:
            raise ValueError('Unknown entry point')
        self.app, self.service, self.entry_point = app, service, entry_point

    def __getattr__(self, name):
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        trace = RequestTiming(scope)
        token = _current.set(trace)
        frame_token = _frame.set(None)
        status, complete, size, failed = 500, False, 0, False

        async def traced_send(message):
            nonlocal status, complete, size
            if message['type'] == 'http.response.start':
                status = message['status']
                headers = [(k, v) for k, v in message.get('headers', [])
                           if k.lower() not in {HEADER, b'server-timing'}]
                headers.append((HEADER, trace.trace_id.encode('ascii')))
                # Only completed auth intervals; backend_app is still open here.
                values = [f"{name};dur={trace.phases[name]['total_ms']:.3f}"
                          for name in ('auth_total', 'auth_lookup', 'auth_fetch', 'auth_verify')
                          if name in trace.phases]
                if values:
                    headers.append((b'server-timing', ', '.join(values).encode('ascii')))
                message = {**message, 'headers': headers}
            await send(message)
            if message['type'] == 'http.response.body':
                size += len(message.get('body', b''))
                complete = not message.get('more_body', False)
                if complete:
                    trace.body_finished_at = perf_counter()

        try:
            with phase('backend_app'):
                await self.app(scope, receive, traced_send)
        except BaseException:
            failed = True
            raise
        finally:
            _current.reset(token)
            _frame.reset(frame_token)
            if trace.enabled:
                method = scope.get('method')
                route = getattr(scope.get('route'), 'path', 'unmatched')
                app_returned_at = perf_counter()
                finished_at = trace.body_finished_at if trace.body_finished_at is not None else app_returned_at
                record = {
                    'event': 'neng_request_timing', 'protocol_version': 1,
                    'service': self.service, 'entry_point': self.entry_point,
                    'trace_id': trace.trace_id, 'span_id': trace.span_id,
                    'id_source': trace.id_source, 'native_request_id': trace.native_id,
                    'route': route, 'method': method if method in
                    {'GET', 'POST', 'PATCH', 'DELETE', 'PUT', 'HEAD', 'OPTIONS'} else 'unknown',
                    'region': os.getenv('AWS_REGION', 'local'),
                    'version': os.getenv('AWS_LAMBDA_FUNCTION_VERSION', 'local'),
                    'status': status, 'body_complete': complete, 'response_bytes': size,
                    'outcome': 'error' if failed or not complete or status >= 400 else 'success',
                    'duration_ms': round((finished_at - trace.started) * 1000, 3),
                    'duration_boundary': 'asgi-final-body-send' if complete else 'app-exit-incomplete',
                    'post_response_ms': round(max(0.0, app_returned_at - finished_at) * 1000, 3),
                    'phases_ms': {name: {key: round(value, 3) if key.endswith('_ms') else value
                                        for key, value in item.items()}
                                  for name, item in trace.phases.items()},
                }
                try:
                    print(json.dumps(record, allow_nan=False), flush=True)
                except OSError:
                    pass  # Diagnostics must not alter a committed write's response.


def correlation_headers(headers):
    """Copy caller headers and retain one safe ID, preferring the active request context."""
    trace = _current.get()
    values = [value for key, value in headers.items() if key.lower() == HEADER.decode()]
    valid = canonical_id(values[0]) if len(values) == 1 else None
    result = {key: value for key, value in headers.items() if key.lower() != HEADER.decode()}
    result[HEADER.decode()] = trace.trace_id if trace else valid or str(uuid4())
    return result
