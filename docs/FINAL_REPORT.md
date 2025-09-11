# Final Report Draft

## Title

Building a Cloud-Native CI/CD Pipeline Using Jenkins, Kubernetes, and GitOps

## Abstract

Short summary of goals: build/test/containerize app with Jenkins, push to GHCR, deploy via Argo CD, apply security scans and monitoring.

## Background

Explain CI/CD, GitOps, Argo CD, Jenkins, container registries, Trivy, Prometheus, Grafana.

## Implementation

- Application: Flask app in `/app`.
- CI: `ci-cd/Jenkinsfile` performs build/test/scan/push/update.
- GitOps: `gitops/argocd-application.yaml` syncs overlays/echo-dev.
- Security: Trivy scans in `ci-cd/trivy-scan.sh` and RBAC in `k8s/rbac.yaml`.
- Monitoring: Prometheus scrape config and Grafana dashboard under `/monitoring`.

## Results

Describe build times, scan results example, deployment status, visualization outcomes.

## Lessons Learned

- Using GitOps simplifies deployments and rollbacks.
- Importance of scanning images and enforcing RBAC.

## References

- Argo CD docs
- Jenkins docs
- Trivy docs
- Prometheus & Grafana docs
