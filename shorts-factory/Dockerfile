# Optional custom RunPod template. The simpler path is RunPod's PyTorch template +
# scripts/runpod_bootstrap.sh (see docs/RUNPOD_GUIDE.md). Build this when you want pods to boot
# ready-to-run with zero setup.
#
# Pick a current tag from https://hub.docker.com/r/runpod/pytorch/tags (CUDA 12.x, Python 3.11).
ARG BASE=runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04
FROM ${BASE}

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ffmpeg sqlite3 fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/shorts-factory
COPY pyproject.toml README.md ./
COPY requirements ./requirements
COPY shorts_factory ./shorts_factory
RUN pip install --no-cache-dir -e ".[gpu]" \
 && python -m venv /opt/venvs/tts \
 && /opt/venvs/tts/bin/pip install --no-cache-dir -r requirements/tts-chatterbox.txt

COPY config ./config
COPY scripts ./scripts
COPY assets ./assets
RUN bash scripts/fetch_fonts.sh

# State, weights and secrets live on the network volume so they survive pod restarts.
ENV HF_HOME=/workspace/hf \
    SHORTS_WORKDIR=/workspace/shorts-data \
    SHORTS_SECRETS_DIR=/workspace/secrets \
    SHORTS_TTS_PYTHON=/opt/venvs/tts/bin/python \
    WORKSPACE=/workspace

# RUN_DAILY=1: produce tomorrow's batch, then stop the pod. Otherwise hand over to RunPod's
# normal start script (SSH/Jupyter) for interactive use.
CMD ["bash", "-c", "if [ \"${RUN_DAILY:-0}\" = 1 ]; then STOP_POD_WHEN_DONE=1 bash scripts/daily_run.sh; elif [ -x /start.sh ]; then exec /start.sh; else sleep infinity; fi"]
