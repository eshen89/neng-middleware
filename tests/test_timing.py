import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from neng_middleware.timing import TimingMiddleware, phase


def make_app():
    app = FastAPI()

    @app.get('/deals/{deal_id}')
    def read(deal_id: str):
        with phase('db_sql'):
            return {'ok': True}

    @app.get('/boom')
    def boom():
        with phase('db_commit'):
            raise RuntimeError('private SQL parameter')

    return TimingMiddleware(app, service='neng-agent', entry_point='public-http')


def test_threadpool_correlation_and_privacy(monkeypatch, capsys):
    monkeypatch.setenv('NENG_TRACE_ENABLED', 'true')
    ids = [str(uuid4()) for _ in range(10)]
    client = TestClient(make_app())
    with ThreadPoolExecutor(max_workers=10) as pool:
        replies = list(pool.map(lambda rid: client.get('/deals/private-party?jwt=secret',
            headers={'x-neng-request-id': rid, 'authorization': 'Bearer secret'}), ids))
    assert [r.headers['x-neng-request-id'] for r in replies] == ids
    output = capsys.readouterr().out
    assert 'secret' not in output and 'private-party' not in output
    records = [json.loads(line) for line in output.splitlines()]
    assert {r['trace_id'] for r in records} == set(ids)
    assert len({r['span_id'] for r in records}) == 10
    for r in records:
        assert r['body_complete'] and r['outcome'] == 'success'
        assert r['route'] == '/deals/{deal_id}'
        assert r['phases_ms']['db_sql']['count'] == 1
        assert r['phases_ms']['backend_app']['total_ms'] >= r['phases_ms']['db_sql']['total_ms']
        assert r['phases_ms']['backend_app']['self_ms'] <= r['phases_ms']['backend_app']['total_ms']


def test_error_and_disabled(monkeypatch, capsys):
    monkeypatch.setenv('NENG_TRACE_ENABLED', 'true')
    result = TestClient(make_app(), raise_server_exceptions=False).get('/boom')
    assert result.status_code == 500 and result.headers['x-neng-request-id']
    output = capsys.readouterr().out
    assert 'private SQL' not in output
    record = json.loads(output)
    assert record['outcome'] == 'error'
    assert record['phases_ms']['db_commit']['errors'] == 1
    monkeypatch.delenv('NENG_TRACE_ENABLED')
    result = TestClient(make_app()).get('/deals/1', headers={'x-neng-request-id': 'invalid'})
    assert result.status_code == 200 and len(result.headers['x-neng-request-id']) == 36
    assert 'server-timing' not in result.headers
    assert capsys.readouterr().out == ''


def test_cancelled_final_send_is_incomplete(monkeypatch, capsys):
    monkeypatch.setenv('NENG_TRACE_ENABLED', 'true')
    async def app(scope, receive, send):
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b'private', 'more_body': False})
    async def send(message):
        if message['type'] == 'http.response.body':
            raise asyncio.CancelledError()
    async def receive():
        return {'type': 'http.request', 'body': b''}
    middleware = TimingMiddleware(app, service='neng-user', entry_point='internal-http')
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(middleware({'type': 'http', 'method': 'GET', 'headers': []}, receive, send))
    record = json.loads(capsys.readouterr().out)
    assert record['body_complete'] is False and record['response_bytes'] == 0
    assert record['outcome'] == 'error'


def test_nested_self_time_is_exclusive(monkeypatch, capsys):
    from neng_middleware import timing
    clock = iter([0.0, 0.0, 0.001, 0.002, 0.004, 0.006, 0.008, 0.010, 0.012])
    monkeypatch.setattr(timing, 'perf_counter', lambda: next(clock))
    monkeypatch.setenv('NENG_TRACE_ENABLED', 'true')
    async def app(scope, receive, send):
        with phase('auth_total'), phase('auth_lookup'):
            pass
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b'', 'more_body': False})
    async def send(message):
        pass
    async def receive():
        return {'type':'http.request'}
    asyncio.run(TimingMiddleware(app, service='neng-agent', entry_point='public-http')(
        {'type':'http','headers':[]},receive,send))
    phases = json.loads(capsys.readouterr().out)['phases_ms']
    assert phases['auth_lookup']['total_ms'] == 2
    assert phases['auth_total']['total_ms'] == 5 and phases['auth_total']['self_ms'] == 3
    assert phases['backend_app']['total_ms'] == 8 and phases['backend_app']['self_ms'] == 3


def test_transport_uses_current_context_and_normal_end_is_not_error(monkeypatch, capsys):
    from test_lambda_http import SDK, Events

    from neng_middleware.lambda_http import invoke_http
    monkeypatch.setenv('NENG_TRACE_ENABLED', 'true')
    app = FastAPI()
    sdk = SDK(Events([b'{"statusCode":200}' + b'\0'*8 + b'ok']))
    @app.get('/me')
    def read():
        result = invoke_http('arn', 'GET', '/me', {'Authorization':'Bearer private',
                            'X-Neng-Request-Id':str(uuid4())}, client=sdk)
        return {'ok': result.read() == b'ok'}
    rid = str(uuid4())
    response = TestClient(TimingMiddleware(app, service='neng-user', entry_point='public-http')).get(
        '/me', headers={'x-neng-request-id':rid})
    assert response.status_code == 200
    assert json.loads(sdk.calls[0]['Payload'])['headers']['x-neng-request-id'] == rid
    record = json.loads(capsys.readouterr().out)
    assert record['phases_ms']['sdk_stream']['errors'] == 0
    assert record['phases_ms']['sdk_headers']['count'] == 1


@pytest.mark.parametrize('headers', [
    [(b'x-neng-request-id', b'private')],
    [(b'x-neng-request-id', str(uuid4()).encode())] * 2,
])
def test_invalid_and_duplicate_ids_are_not_echoed(headers, monkeypatch, capsys):
    monkeypatch.setenv('NENG_TRACE_ENABLED','true')
    async def app(scope, receive, send):
        await send({'type':'http.response.start','status':404,'headers':[]})
        await send({'type':'http.response.body','body':b''})
    async def receive():
        return {'type':'http.request'}
    async def send(message):
        pass
    asyncio.run(TimingMiddleware(app, service='neng-agent', entry_point='internal-http')(
        {'type':'http','headers':headers},receive,send))
    record = json.loads(capsys.readouterr().out)
    assert record['id_source'] == 'generated'
    assert all(record['trace_id'] != value.decode() for _, value in headers)


def test_real_jwks_fetch_and_verify_are_separate(monkeypatch, capsys):
    import jwt
    from test_auth import _jwks, _token

    from neng_middleware import auth
    auth.reset_jwks_cache()
    monkeypatch.setenv('NENG_TRACE_ENABLED','true')
    monkeypatch.setenv('SUPABASE_JWKS_URL','https://proj.supabase.co/auth/v1/.well-known/jwks.json')
    monkeypatch.setenv('SUPABASE_JWT_ISSUER','https://proj.supabase.co/auth/v1')
    monkeypatch.delenv('NENG_AUTH_DISABLED',raising=False)
    # The real PyJWKClient cache executes; only its network result is replaced.
    def fetch(self):
        self.jwk_set_cache.put(_jwks())
        return _jwks()
    monkeypatch.setattr(jwt.PyJWKClient,'fetch_data',fetch)
    app = FastAPI()
    @app.get('/me')
    def read():
        assert auth.current_user('Bearer ' + _token()) == 'user-123'
        return {'ok':True}
    client = TestClient(TimingMiddleware(app,service='neng-user',entry_point='public-http'))
    try:
        for attempt in range(2):
            assert client.get('/me').status_code == 200
            record = json.loads(capsys.readouterr().out)
            assert ('auth_fetch' in record['phases_ms']) == (attempt == 0)
            assert record['phases_ms']['auth_verify']['count'] == 1
    finally:
        auth.reset_jwks_cache()


def test_body_boundary_clips_cleanup_and_preserves_final_send_wait(monkeypatch, capsys):
    from neng_middleware import timing
    clock = [0.0]
    monkeypatch.setattr(timing, 'perf_counter', lambda: clock[0])
    monkeypatch.setenv('NENG_TRACE_ENABLED','true')
    async def app(scope, receive, send):
        await send({'type':'http.response.start','status':200,'headers':[]})
        clock[0]=.010
        await send({'type':'http.response.body','body':b'ok'})
        clock[0]=600
        with timing.phase('db_sql'):
            clock[0]=601  # Post-response background work is not request-body latency.
    async def receive():
        return {'type':'http.disconnect'}
    async def send(message):
        if message['type']=='http.response.body':clock[0]=.025
    asyncio.run(TimingMiddleware(app,service='neng-agent',entry_point='internal-http')(
        {'type':'http','method':'GET','headers':[]},receive,send))
    record=json.loads(capsys.readouterr().out)
    assert record['duration_ms']==25
    assert record['phases_ms']['backend_app']['total_ms']==25
    assert 'db_sql' not in record['phases_ms']
    assert record['body_complete']
