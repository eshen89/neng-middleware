import io
import json

import pytest

from neng_middleware.lambda_http import LambdaHTTPError, invoke_http


class Events:
    def __init__(self, payloads, error=None, complete=True):
        self.payloads, self.error, self.complete, self.closed = payloads, error, complete, False
    def __iter__(self):
        for payload in self.payloads:
            yield {"PayloadChunk": {"Payload": payload}}
        if self.complete:
            yield {"InvokeComplete": {"ErrorCode": self.error} if self.error else {}}
    def close(self):
        self.closed = True


class SDK:
    def __init__(self, events=None):
        self.events, self.calls = events, []
    def invoke_with_response_stream(self, **kwargs):
        self.calls.append(kwargs)
        return {"EventStream": self.events}
    def invoke(self, **kwargs):
        self.calls.append(kwargs)
        self.body = io.BytesIO(json.dumps({"statusCode": 201, "headers": {}, "body": "aGk=", "isBase64Encoded": True}).encode())
        return {"Payload": self.body}


def test_split_metadata_stream_is_not_buffered_and_closes():
    head = json.dumps({"statusCode": 200, "headers": {"content-type": "text/event-stream"}}).encode()
    events = Events([head[:5], head[5:] + b'\0' * 4, b'\0' * 4 + b'one', b'two'])
    sdk = SDK(events)
    result = invoke_http("arn", "POST", "/chat", {"authorization": "Bearer user"}, client=sdk)
    assert result.status == 200
    assert next(result.chunks) == b'one'
    assert not events.closed
    assert result.read() == b'two' and events.closed
    assert json.loads(sdk.calls[0]["Payload"])["headers"]["authorization"] == "Bearer user"


@pytest.mark.parametrize("events", [Events([b'bad']), Events([b'{}' + b'\0'*8]), Events([b'{}' * 40000])])
def test_invalid_metadata_closes_stream(events):
    with pytest.raises((LambdaHTTPError, KeyError)):
        invoke_http("arn", "GET", "/", {}, client=SDK(events))
    assert events.closed


@pytest.mark.parametrize("error,complete", [("Unhandled", True), (None, False)])
def test_incomplete_or_failed_stream_is_not_success(error, complete):
    events = Events([b'{"statusCode":200}' + b'\0'*8 + b'partial'], error, complete)
    result = invoke_http("arn", "GET", "/", {}, client=SDK(events))
    with pytest.raises(LambdaHTTPError):
        result.read()
    assert events.closed


def test_buffered_base64_response_and_payload_closed():
    sdk = SDK()
    result = invoke_http("arn", "GET", "/me", {}, streaming=False, client=sdk)
    assert result.status == 201 and result.read() == b'hi' and sdk.body.closed


def test_serialized_limit_checked_before_sdk_call():
    sdk = SDK()
    with pytest.raises(LambdaHTTPError, match="payload limit"):
        invoke_http("arn", "POST", "/", {"huge": "x" * (6*1024*1024)}, client=sdk)
    assert sdk.calls == []


def test_close_before_consuming_body_releases_sdk_stream():
    events = Events([b'{"statusCode":200}' + b'\0'*8 + b'body'])
    result = invoke_http('arn', 'GET', '/', {}, client=SDK(events))
    result.close()
    assert events.closed
