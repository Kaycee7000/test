import json

import pytest

from shorts_factory.config import load_channels, load_settings
from shorts_factory.publish.storage import B2Store, LocalStore, cleanup_local, object_base, slugify


def _job(**kw):
    base = {"id": "20260923-history_es-abc123", "publish_date": "2026-09-23", "format": "untold_story",
            "hook_type": "question", "slot_utc": "2026-09-23T15:28:00+00:00", "title": "t", "dir": "/tmp/x"}
    return base | kw


def test_slugify_handles_accents_and_symbols():
    assert slugify("¿Por qué desapareció el pueblo en 1908?") == "por-que-desaparecio-el-pueblo-en-1908"
    assert slugify("!!!") == "video"
    assert len(slugify("a" * 200)) == 60


def test_keys_group_by_niche_then_language_then_date(project):
    s = load_settings(project / "config" / "settings.yaml")
    chs = {c.id: c for c in load_channels(s)}
    es = object_base(s, chs["history_es"], _job(), "El pueblo que desapareció")
    assert es == "history/es/2026-09-23/20260923-history_es-abc123_el-pueblo-que-desaparecio"
    s.storage.prefix = "shorts/"
    money = object_base(s, chs["money_en"], _job(id="j1"), "Blockbuster's $50M mistake")
    assert money == "shorts/money/en/2026-09-23/j1_blockbuster-s-50m-mistake"


def test_cleanup_modes(tmp_path):
    for name in ["images/a.png", "voice/s.wav", "final.mp4", "preview.jpg", "script.json", "words.json"]:
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    cleanup_local(tmp_path, "media")
    left = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file())
    assert left == ["script.json", "words.json"]
    cleanup_local(tmp_path, "all")
    assert not tmp_path.exists()


def test_local_store_mirrors_layout(project, tmp_path):
    s = load_settings(project / "config" / "settings.yaml")
    s.storage.local_dir = str(tmp_path / "out")
    store = LocalStore(s)
    src = tmp_path / "v.mp4"
    src.write_bytes(b"123")
    store.put(src, "history/en/d/x.mp4", "video/mp4")
    assert (tmp_path / "out/history/en/d/x.mp4").read_bytes() == b"123"


def test_b2_store_uploads_verifies_and_presigns(project, tmp_path, monkeypatch):
    moto = pytest.importorskip("moto")
    import boto3

    monkeypatch.setenv("B2_KEY_ID", "kid")
    monkeypatch.setenv("B2_APPLICATION_KEY", "secret")
    s = load_settings(project / "config" / "settings.yaml")
    s.storage.bucket, s.storage.endpoint = "shorts", None  # moto intercepts the default endpoint
    with moto.mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="shorts")
        store = B2Store(s)
        sent_headers = []
        store.s3.meta.events.register("before-send.s3", lambda request, **_: sent_headers.append(dict(request.headers)))
        video = tmp_path / "final.mp4"
        video.write_bytes(b"\x00" * 4096)
        store.put(video, "history/en/2026-09-23/x.mp4", "video/mp4")
        store.put_bytes(json.dumps({"a": 1}).encode(), "history/en/2026-09-23/x.json", "application/json")
        head = store.s3.head_object(Bucket="shorts", Key="history/en/2026-09-23/x.mp4")
        assert head["ContentLength"] == 4096 and head["ContentType"] == "video/mp4"
        assert "X-Amz-Expires=604800" in store.url("history/en/2026-09-23/x.mp4")
        assert store.check() == "b2://shorts"
        # B2 rejects the flexible-checksum headers newer boto3 adds by default
        assert sent_headers  # the hook really saw the requests
        assert not any("x-amz-sdk-checksum-algorithm" in {k.lower() for k in h} for h in sent_headers)


def test_b2_store_requires_credentials(project, monkeypatch):
    monkeypatch.delenv("B2_KEY_ID", raising=False)
    s = load_settings(project / "config" / "settings.yaml")
    with pytest.raises(Exception, match="B2_KEY_ID"):
        B2Store(s)
