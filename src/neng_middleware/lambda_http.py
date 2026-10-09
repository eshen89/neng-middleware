"""HTTP contract over IAM Lambda Invoke, with Lambda Web Adapter streaming support.

No Function URL, static credential, auth bypass, or automatic write retry. The caller's
execution role signs SDK requests. The end-user JWT remains in the HTTP event.
"""
from __future__ import annotations

import base64
import json
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache

import boto3
from botocore.config import Config


class LambdaHTTPError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def lambda_client():
    return boto3.client("lambda", config=Config(
        connect_timeout=5, read_timeout=360, max_pool_connections=32,
        retries={"total_max_attempts": 1, "mode": "standard"},
    ))


def http_event(method: str, path: str, headers: dict[str, str], body: bytes = b"",
               query: str = "") -> dict:
    return {
        "version": "2.0", "routeKey": "$default", "rawPath": path,
        "rawQueryString": query, "headers": headers,
        "requestContext": {
            "routeKey": "$default", "stage": "$default", "domainName": "internal.neng",
            "requestId": uuid.uuid4().hex, "timeEpoch": int(time.time() * 1000),
            "http": {"method": method, "path": path, "protocol": "HTTP/1.1",
                     "sourceIp": "127.0.0.1", "userAgent": "neng-lambda-invoke"},
        },
        "body": base64.b64encode(body).decode(), "isBase64Encoded": True,
    }


@dataclass
class HTTPResult:
    status: int
    headers: dict[str, str]
    chunks: Iterator[bytes]

    def read(self) -> bytes:
        try:
            return b"".join(self.chunks)
        finally:
            self.close()

    def close(self):
        close = getattr(self.chunks, "close", None)
        if close:
            close()

    def json(self):
        return json.loads(self.read())


class _ClosingIterator:
    """Close the SDK stream even if the body has never been iterated."""

    def __init__(self, iterator, close):
        self.iterator = iterator
        self.cleanup = close

    def __iter__(self):
        return self

    def __next__(self):
        try:
            return next(self.iterator)
        except BaseException:
            self.close()
            raise

    def close(self):
        self.iterator.close()
        self.cleanup()


def _chunks(events) -> Iterator[bytes]:
    try:
        complete = False
        for event in events:
            if "PayloadChunk" in event:
                yield event["PayloadChunk"]["Payload"]
            elif "InvokeComplete" in event:
                complete = True
                if event["InvokeComplete"].get("ErrorCode"):
                    raise LambdaHTTPError("Internal response stream failed")
        if not complete:
            raise LambdaHTTPError("Internal response stream ended without completion")
    finally:
        close = getattr(events, "close", None)
        if close:
            close()


def invoke_http(function: str, method: str, path: str, headers: dict[str, str],
                body: bytes = b"", query: str = "", *, streaming: bool = True,
                client=None) -> HTTPResult:
    if not function:
        raise LambdaHTTPError("Internal function is not configured")
    payload = json.dumps(http_event(method, path, headers, body, query)).encode()
    if len(payload) > 6 * 1024 * 1024:
        raise LambdaHTTPError("Serialized request exceeds Lambda's synchronous payload limit")
    sdk = client or lambda_client()
    if not streaming:
        response = sdk.invoke(FunctionName=function, InvocationType="RequestResponse", Payload=payload)
        stream = response["Payload"]
        try:
            data = json.loads(stream.read())
        finally:
            stream.close()
        if response.get("FunctionError"):
            raise LambdaHTTPError("Internal function failed")
        raw = data.get("body", "")
        content = base64.b64decode(raw) if data.get("isBase64Encoded") else raw.encode()
        return HTTPResult(int(data["statusCode"]), data.get("headers", {}), iter([content]))

    response = sdk.invoke_with_response_stream(FunctionName=function, Payload=payload)
    chunks = _chunks(response["EventStream"])
    prefix = b""
    try:
        for chunk in chunks:
            prefix += chunk
            if b"\x00" * 8 in prefix:
                metadata, first = prefix.split(b"\x00" * 8, 1)
                if len(metadata) > 65536:
                    raise LambdaHTTPError("Invalid internal response metadata")
                head = json.loads(metadata)
                status = int(head["statusCode"])
                if not 100 <= status <= 599 or not isinstance(head.get("headers", {}), dict):
                    raise LambdaHTTPError("Invalid internal response metadata")
                break
            if len(prefix) > 65536:
                raise LambdaHTTPError("Invalid internal response metadata")
        else:
            raise LambdaHTTPError("Missing internal response metadata")
    except BaseException:
        chunks.close()
        raise

    def body_chunks():
        try:
            if first:
                yield first
            yield from chunks
        finally:
            chunks.close()

    # Current Neng APIs authenticate with JWTs and do not issue cookies. Never pass service
    # cookies to the browser, or trust an upstream header to choose a destination.
    return HTTPResult(status, head.get("headers", {}), _ClosingIterator(body_chunks(), chunks.close))
