# Setup Guide

This guide walks through installing the components and deploying the sample pipeline. Replace placeholder values (YOUR_GITHUB_ORG, YOUR_GITOPS_REPO, YOUR_IMAGE_REPO) before running.

Prerequisites:
- A Kubernetes cluster (kind, minikube, or cloud provider)
- kubectl configured to talk to the cluster
- Jenkins server with Docker (or access to a Docker daemon)
- Argo CD installed in the cluster
- Prometheus and Grafana installed (or use the kube-prometheus-stack)
- Git and access to GitHub repos: app repo and gitops repo

1) Create namespaces and RBAC

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/rbac.yaml
```

2) Install Argo CD (if not installed)

# Placeholder command (see Argo CD docs)
```bash
# kubectl create namespace argocd
# kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
```

3) Configure Jenkins

- Create a Jenkins pipeline with the provided `ci-cd/Jenkinsfile`.
- Configure credentials for GHCR (username=GITHUB_ACTOR, password=PERSONAL_ACCESS_TOKEN) and Git access.

4) Run a build

From Jenkins, run the pipeline. The steps will run: test → trivy → docker build → push → update gitops.

5) Install Prometheus & Grafana

Using Helm (example):

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update
helm install prometheus prometheus-community/prometheus
helm install grafana grafana/grafana
```

6) Import Grafana dashboard

In Grafana, create a new dashboard and paste the JSON from `monitoring/grafana-dashboard.json`.

Screenshots: (placeholders)
- screenshot-jenkins-pipeline.png
- screenshot-argocd-sync.png
- screenshot-grafana-dashboard.png

7) Deploy to Vercel (optional)

This repository includes a lightweight Vercel serverless function at `api/echo.py` so you can deploy the app to Vercel without running Flask.

```bash
# Install Vercel (if not installed)
npm i -g vercel

# Deploy (interactive login may be required)
vercel --prod
```

Vercel will use `vercel.json` to route `/` and `/health` to the serverless function. The function returns JSON similar to the Flask app.

