import json
import werkzeug
from app import app


def _ensure_werkzeug_version():
    # Newer werkzeug builds may not expose __version__ attribute expected by Flask testing.
    if not hasattr(werkzeug, '__version__'):
        setattr(werkzeug, '__version__', '999')


def test_index():
    _ensure_werkzeug_version()
    client = app.test_client()
    resp = client.get('/')
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'message' in data
    assert data['message'].startswith('Hello')
