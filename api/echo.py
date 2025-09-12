"""
Vercel Python Serverless function compatible with multiple invocation patterns.

This file exports two callables to improve compatibility with the Vercel builders:
- handler(request): the standard Vercel function entrypoint (returns a dict)
- app(environ, start_response): a WSGI callable which some runtimes may invoke

Both paths include robust error handling and will return a JSON body with traceback
on error to make debugging easier when the function logs aren't immediately available.

To deploy to Vercel, run `vercel` in the repo root (after installing/vercel and login).
"""
import os
import json
import traceback


def _make_payload():
    return {
        "message": "Hello from Echo App (Vercel)",
        "version": os.getenv("APP_VERSION", "v0.1"),
        "pod": os.getenv("POD_NAME", "vercel")
    }


def handler(request=None):
    """Vercel-style handler. Some Vercel Python runtimes expect this callable.

    Returns a dict with statusCode, headers and body.
    """
    try:
        payload = _make_payload()
        return {
            "statusCode": 200,
            "headers": {"content-type": "application/json"},
            "body": json.dumps(payload)
        }
    except Exception as exc:  # pragma: no cover - defensive in runtime
        tb = traceback.format_exc()
        err = {"error": str(exc), "traceback": tb}
        return {"statusCode": 500, "headers": {"content-type": "application/json"}, "body": json.dumps(err)}


def app(environ, start_response):
    """WSGI-compatible entrypoint. Some runtimes call the module as a WSGI app.

    This function returns an iterable of bytes as per WSGI spec.
    """
    try:
        payload = _make_payload()
        body = json.dumps(payload).encode("utf-8")
        start_response("200 OK", [("Content-Type", "application/json")])
        return [body]
    except Exception as exc:  # pragma: no cover - defensive in runtime
        tb = traceback.format_exc()
        err = {"error": str(exc), "traceback": tb}
        body = json.dumps(err).encode("utf-8")
        start_response("500 Internal Server Error", [("Content-Type", "application/json")])
        return [body]

