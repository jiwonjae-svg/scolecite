# Railway — Production Deployment Guide

> **Target:** [Railway](https://railway.app/)  
> **Stack:** Web Service (Docker) + PostgreSQL  
> **Secrets:** Railway environment variables (no .env in production)

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Create a Railway Project](#2-create-a-railway-project)
3. [Add PostgreSQL](#3-add-postgresql)
4. [Deploy the App](#4-deploy-the-app)
5. [Configure Environment Variables](#5-configure-environment-variables)
6. [Custom Domain & HTTPS](#6-custom-domain--https)
7. [Health Checks](#7-health-checks)
8. [Logs & Monitoring](#8-logs--monitoring)
9. [Cost & Limits](#9-cost--limits)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. Prerequisites

| Item | Notes |
|------|--------|
| **Railway account** | Sign up at [railway.app](https://railway.app) |
| **GitHub repo** | Code in a GitHub repository |
| **Dockerfile** | Project root has a `Dockerfile` (Railway auto-detects it) |

No local Docker or `gcloud` required. Railway builds and runs from the repo.

---

## 2. Create a Railway Project

1. Log in to [Railway Dashboard](https://railway.app/dashboard)
2. Click **New Project**
3. Choose **Deploy from GitHub repo**
4. Select your repository (e.g. `jiwonjae-svg/scolecite`)
5. Railway will create a new **service** from the repo (builds using the root `Dockerfile`)

---

## 3. Add PostgreSQL

1. In the same project, click **+ New** → **Database** → **PostgreSQL**
2. Railway provisions a PostgreSQL instance and exposes:
   - `DATABASE_URL` (e.g. `postgresql://postgres:...@...railway.internal:5432/railway`)
   - `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, `PGDATABASE`
3. **Link the database to your app:**
   - Open your **Web Service** (Scolecite)
   - Go to **Variables**
   - Click **Add Reference** and add `DATABASE_URL` from the PostgreSQL service  
   (Or the project’s **Variables** tab may already show `DATABASE_URL` when services are in the same project.)

**Connection string format for the app:**  
The app expects `postgresql+asyncpg://...`. Railway’s `DATABASE_URL` is usually `postgresql://...`. If your app uses `asyncpg`, set:

```bash
DATABASE_URL=postgresql+asyncpg://user:password@host:port/railway
```

You can derive this from Railway’s `DATABASE_URL` by replacing the scheme:  
`postgresql://` → `postgresql+asyncpg://`.

---

## 4. Deploy the App

1. **Service source**
   - Service is created from the GitHub repo; branch is typically `main`.
2. **Build**
   - Railway runs `docker build` from the repo root (uses `Dockerfile`).
   - Build logs are in the service **Deployments** tab.
3. **Run**
   - Railway sets `PORT` (e.g. `8000`). The Dockerfile `CMD` uses `PORT`, so no change needed.
4. **Redeploy**
   - Push to the connected branch, or use **Redeploy** in the dashboard.

---

## 5. Configure Environment Variables

In the **Web Service** → **Variables** (or project **Variables**), set:

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | From PostgreSQL service reference, or `postgresql+asyncpg://...` |
| `ANTHROPIC_API_KEY` | Yes | Anthropic (Claude) API key |
| `XAI_GROK_API_KEY` | Yes | xAI Grok API key |
| `APCA_API_KEY_ID` | Yes | Alpaca API key ID |
| `APCA_API_SECRET_KEY` | Yes | Alpaca API secret |
| `APCA_API_BASE_URL` | No | Default `https://paper-api.alpaca.markets` (paper) |
| `TRADING_MODE` | No | `paper` or `live` |
| `POLYGON_API_KEY` | No | Optional market data |
| `GUNICORN_WORKERS` | No | Default `2` (Railway concurrency; 4 if on a larger plan) |

Do **not** commit `.env` to the repo; use only Railway variables.

---

## 6. Custom Domain & HTTPS

1. Open the Web Service → **Settings** → **Networking**
2. Under **Public Networking**, click **Generate Domain**
3. Railway assigns a URL like `xxx.up.railway.app` with HTTPS
4. Optional: add a **Custom Domain** and point DNS (CNAME) as instructed

HTTPS is provided by Railway; no Nginx or Certbot needed.

---

## 7. Health Checks

| Endpoint | Purpose |
|----------|--------|
| `GET /health` | Liveness (no DB) |
| `GET /ready` | Readiness (DB connected) |

```bash
curl https://YOUR_RAILWAY_URL/health
# → {"status":"ok","mode":"paper"}

curl https://YOUR_RAILWAY_URL/ready
# → {"status":"ready","db":"connected"}
```

In Railway you can configure a **Health Check** path (e.g. `/health`) in the service settings if supported.

---

## 8. Logs & Monitoring

- **Logs:** Web Service → **Deployments** → select a deployment → **View Logs** (stdout/stderr).
- **Metrics:** Dashboard shows CPU/memory and request metrics.
- **Alerts:** Configure in Railway project settings if available.

---

## 9. Cost & Limits

- **Free tier:** Limited execution time and resources; suitable for dev/demo.
- **Paid plan:** Usage-based (CPU, memory, egress). PostgreSQL and web service are billed separately.
- Check [Railway Pricing](https://railway.app/pricing) for current limits and pricing.

---

## 10. Troubleshooting

### Build fails

- Ensure `Dockerfile` is at repo root and has no syntax errors.
- Check build logs for missing files (e.g. `shared/`, `server/`, `requirements.txt`).

### App exits or 503

- Confirm `PORT` is used in the app (Railway sets it); the Dockerfile already uses `PORT`.
- Check **Variables**: `DATABASE_URL` and API keys must be set.
- Use **View Logs** for tracebacks.

### Database connection errors

- Ensure PostgreSQL service is running and `DATABASE_URL` is referenced in the Web Service.
- Use `postgresql+asyncpg://...` if the app uses asyncpg.
- For same-project services, use the internal hostname Railway provides (e.g. `*.railway.internal`).

### Desktop client cannot connect

- Use the **public** Railway URL (e.g. `https://xxx.up.railway.app`) in the client’s Server URL.
- Ensure the service has **Public Networking** and a generated domain.

---

## Quick Reference

| Action | Where |
|--------|--------|
| Change env vars | Service → Variables |
| Redeploy | Deployments → Redeploy, or push to GitHub |
| Logs | Deployments → deployment → View Logs |
| DB connection string | PostgreSQL service → Connect → `DATABASE_URL` |
| Public URL | Service → Settings → Networking → Generate Domain |
