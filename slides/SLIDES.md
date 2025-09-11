# Building a Cloud-Native CI/CD Pipeline Using Jenkins, Kubernetes, and GitOps

--

Title Slide

- Title: Building a Cloud-Native CI/CD Pipeline Using Jenkins, Kubernetes, and GitOps
- Presenter: YOUR_NAME
- Date: 2025-09-11

--

Problem Statement

- Traditional deployments are slow and error-prone.
- Need automated, observable, and secure pipeline for cloud-native apps.

--

Solution Overview

- Jenkins for CI (build, test, scan, push)
- GitHub Container Registry to host images
- GitOps (Argo CD) to deploy to Kubernetes
- Trivy for security scanning
- Prometheus & Grafana for monitoring

--

Tools & Tech Stack

- Jenkins, Docker, GitHub Container Registry
- Kubernetes, Argo CD, Kustomize
- Trivy, Prometheus, Grafana

--

Pipeline Architecture

- Checkout → Build → Test → Trivy → Docker Build → Push to GHCR → Update GitOps Repo → Argo CD Sync

--

Security & RBAC

- Trivy scan stage catches vulnerabilities
- RBAC restricts deployment to `ci-deployer` service account

--

Monitoring

- Prometheus scrapes the app
- Grafana dashboard shows CPU, memory, pod readiness

--

Demo Results

- Build and push logs (screenshots)
- Argo CD sync status (screenshots)
- Grafana dashboard (screenshot)

--

Conclusion

- GitOps + Jenkins + security scanning creates robust pipeline.
