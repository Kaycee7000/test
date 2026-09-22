"""Run GPU stages as short-lived subprocesses.

Each stage (TTS, alignment, images, animation) gets a JSON manifest with every task for the batch,
loads its model ONCE, processes everything, and exits. Benefits:
  * VRAM is fully released between stages (no fragmentation or leaks across models),
  * a crash in one model cannot take down the orchestrator,
  * a stage can run in a different venv (Chatterbox pins torch 2.6 / diffusers 0.29).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

WORKERS_DIR = Path(__file__).resolve().parent.parent / "workers"
PACKAGE_ROOT = WORKERS_DIR.parent.parent


class WorkerError(RuntimeError):
    pass


def resolve_python(configured: str | None, required: bool) -> str:
    if configured and Path(configured).exists():
        return configured
    if configured and required:
        raise WorkerError(
            f"worker interpreter {configured!r} not found. Run scripts/runpod_bootstrap.sh "
            f"(creates the isolated TTS and music venvs) or fix the `python` path in config/settings.yaml."
        )
    return sys.executable


def run_worker(name: str, manifest: dict[str, Any], scratch: Path, python: str | None = None,
               require_python: bool = False) -> dict[str, dict[str, Any]]:
    scratch.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    mpath = scratch / f"{name}-{stamp}-{os.getpid()}.json"
    opath = mpath.with_suffix(".out.json")
    mpath.write_text(json.dumps(manifest, indent=1))
    exe = resolve_python(python, require_python)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PACKAGE_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    cmd = [exe, str(WORKERS_DIR / f"{name}_worker.py"), str(mpath), str(opath)]
    log.info("worker %s: %d tasks (%s)", name, len(manifest.get("tasks", [])), exe)
    t0 = time.time()
    proc = subprocess.run(cmd, env=env)
    if not opath.exists():
        raise WorkerError(f"{name} worker exited with {proc.returncode} and produced no results")
    results = json.loads(opath.read_text())["results"]
    ok = sum(1 for r in results.values() if r.get("ok"))
    log.info("worker %s: %d/%d ok in %.1fs", name, ok, len(results), time.time() - t0)
    return results
