"""
Vercel Python Serverless function compatible with the Vercel runtime.

This provides a lightweight endpoint for deployments to Vercel without requiring Flask.

To deploy to Vercel, simply `vercel` in the repo root (after installing/vercel login).
"""
import os
import json


def handler(request):
    # request is a Vercel request-like mapping. We avoid external dependencies.
    payload = {
        "message": "Hello from Echo App (Vercel)",
        "version": os.getenv("APP_VERSION", "v0.1"),
        "pod": os.getenv("POD_NAME", "vercel")
    }
    return {
        "statusCode": 200,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(payload)
    }
