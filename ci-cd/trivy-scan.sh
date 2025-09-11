#!/usr/bin/env bash
set -euo pipefail
echo "Running Trivy filesystem scan on app/ and Docker image placeholder"

# Example scanning filesystem
trivy fs --exit-code 0 --no-progress app/ || true

# Example scanning the built image (placeholder name)
IMAGE="ghcr.io/YOUR_GITHUB_ORG/YOUR_IMAGE_REPO:latest"
echo "Would scan image: $IMAGE"
trivy image --exit-code 0 --no-progress "$IMAGE" || true

echo "-- Trivy scan completed (sample output below) --"
cat <<'EOF'
2025-09-11T00:00:00.000Z	UNKNOWN	CVE-2020-1234	High	...
2025-09-11T00:00:01.000Z	FOUND	CVE-2021-5678	Medium	...
EOF

exit 0
