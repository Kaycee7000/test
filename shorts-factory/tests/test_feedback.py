import json

from shorts_factory.config import load_settings
from shorts_factory.content.strategy import learnings_text, outcomes
from shorts_factory.db import DB
from shorts_factory.publish.feedback import import_from_store, import_rows, parse_csv
from shorts_factory.publish.storage import LocalStore


def _db(tmp_path, n=5):
    db = DB(tmp_path / "shorts.db")
    ids = []
    for i in range(n):
        jid = db.create_job("history_en", None, "2026-09-10", "untold_story" if i % 2 else "unsolved",
                            f"The lost city number {i} of the desert", tmp_path)
        db.update_job(jid, state="uploaded", title=f"The lost city number {i} of the desert", hook_type="question",
                      slot_utc="2026-09-10T13:00:00+00:00", uploaded_at="2026-09-09T20:00:00+00:00",
                      data={"hook": f"hook {i}"})
        ids.append(jid)
    return db, ids


def test_filled_manifest_rows_match_by_id(tmp_path):
    db, ids = _db(tmp_path)
    csv_text = "id,title,posted_url,posted_at,views,avg_view_pct,likes\n" + "\n".join(
        f"{jid},x,https://www.youtube.com/shorts/{'V' * 10}{i},2026-09-10 13:05,{(i + 1) * 1000},{60 + i * 5}%,\"1,234\""
        for i, jid in enumerate(ids)) + f"\n{ids[0]},x,,,,,\n{ids[1]},x,,,,,,extra,cells\n"  # unfilled/ragged rows skipped
    matched, unmatched = import_rows(db, parse_csv(csv_text.encode("utf-8-sig")))
    assert (matched, unmatched) == (5, 0)
    assert db.job(ids[2])["video_id"] == "VVVVVVVVVV2"
    perf = db.performance("history_en")
    assert len(perf) == 5 and {p["views"] for p in perf} == {1000, 2000, 3000, 4000, 5000}
    assert outcomes(perf, "format") and "WHAT IS WORKING" in learnings_text(perf)  # learning loop is back on


def test_studio_export_matches_by_title_and_skips_total(tmp_path):
    db, ids = _db(tmp_path, n=2)
    export = ("Content,Video title,Video publish time,Views,Average percentage viewed (%),Average view duration\n"
              "Total,,,99999,,\n"
              "ABCDEFGHIJK,The Lost City Number 1 of the Desert,\"Sep 10, 2026\",\"12,500\",88.4,0:00:41\n"
              "ZZZZZZZZZZZ,A video we did not make,\"Sep 10, 2026\",50,10,0:00:05\n")
    matched, unmatched = import_rows(db, parse_csv(export.encode()))
    assert (matched, unmatched) == (1, 1)
    row = db.conn.execute("SELECT * FROM metrics WHERE video_id='ABCDEFGHIJK'").fetchone()
    assert row["views"] == 12500 and row["avg_view_pct"] == 88.4 and row["avg_view_duration"] == 41


def test_import_from_storage_is_idempotent(project, tmp_path):
    s = load_settings(project / "config" / "settings.yaml")
    s.storage.local_dir = str(tmp_path / "bucket")
    store = LocalStore(s)
    db, ids = _db(tmp_path)
    store.put_bytes(f"id,views,avg_view_pct\n{ids[0]},777,70\n".encode(), "feedback/week1.csv", "text/csv")
    assert import_from_store(store, "feedback/", db)[0]["matched"] == 1
    assert import_from_store(store, "feedback/", db) == []  # same file is not imported twice
    assert json.loads(json.dumps(db.performance("history_en")))[0]["views"] == 777
