# Architecture Diagram

This document describes the architecture for the Cloud-Native CI/CD pipeline.

Overview:

- Jenkins (CI): checks out code from `YOUR_GITHUB_REPO`, runs tests, runs Trivy, builds Docker image, pushes to GHCR.
- GitHub Container Registry (GHCR): stores built images under `ghcr.io/YOUR_GITHUB_ORG/YOUR_IMAGE_REPO`.
- GitOps Repo: contains Kubernetes manifests and kustomize overlays. Jenkins updates image tag in this repo.
- Argo CD: watches the GitOps repo and syncs changes into the Kubernetes cluster (namespace `echo-dev`). Auto-sync is enabled.
- Kubernetes: runs the application in `echo-dev` namespace with RBAC restricting deploy permissions to a `ci-deployer` ServiceAccount.
- Monitoring: Prometheus scrapes the app and cluster metrics; Grafana dashboards visualize CPU, memory, and pod status.

See `docs/diagram-placeholder.png` (placeholder) for the visual diagram.
