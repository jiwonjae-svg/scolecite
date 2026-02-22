<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white" alt="FastAPI" />
  <img src="https://img.shields.io/badge/Claude_Opus-191919?style=for-the-badge&logo=anthropic&logoColor=white" alt="Claude" />
  <img src="https://img.shields.io/badge/Grok-000000?style=for-the-badge&logo=x&logoColor=white" alt="Grok" />
  <img src="https://img.shields.io/badge/Alpaca-FFDC00?style=for-the-badge&logo=alpaca&logoColor=black" alt="Alpaca" />
  <img src="https://img.shields.io/badge/license-MIT-green?style=for-the-badge" alt="License" />
</p>

<h1 align="center">◆ Project Scolecite</h1>

<p align="center">
  <strong>Autonomous AI Quant Trading Terminal for the US Stock Market</strong>
  <br />
  <em>3-Model AI Architecture · Dynamic Universe · Real-Time WebSocket · AI Streaming · Auto Journal</em>
</p>

> **⚠️ DISCLAIMER** — This project is for **educational and research purposes only**.
> The authors bear **NO responsibility** for any financial losses.
> Always use paper trading mode during development and testing.

---

## 📋 Table of Contents

- [Overview](#-overview)
- [Architecture](#-architecture)
- [Features](#-features)
- [Project Structure](#-project-structure)
- [Quick Start](#-quick-start)
- [Environment Variables](#-environment-variables)
- [AI Model Roles](#-ai-model-roles)
- [Risk Management](#-risk-management)
- [MCP Tools](#-mcp-tools)
- [API Reference](#-api-reference)
- [Desktop Client](#-desktop-client)
- [Cloud Run Deployment](#-cloud-run-deployment)
- [Tech Stack](#-tech-stack)
- [License](#-license)

---

## 🔭 Overview

Project Scolecite is a full-stack Python autonomous trading system powered by a **3-model AI architecture** running in continuous feedback loops:

| Phase | Model | Role |
|-------|-------|------|
| 🔍 **Scan** | Grok Fast (`grok-3-fast`) | Rapid news, technical, & social data ingestion |
| 🧠 **Brainstorm** | Grok Strategy (`grok-3`) | Generate trading hypotheses with confidence scores |
| 👔 **Decide** | Opus CEO (`claude-opus-4`) | Review hypotheses, approve/reject, execute via MCP tools |
| 🔄 **Correct** | Opus CEO | Review past trades, write self-correction improvements |

The system is split into a **FastAPI server** (deployable to Cloud Run) and a **customtkinter desktop client** connected via REST + SSE.

---

## 🏗 Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                       ORCHESTRATOR (4 Loops)                     │
│                                                                  │
│  Loop 1 ─ Data Collection         Loop 2 ─ Strategy Cycle       │
│  ┌──────────────┐                 ┌──────────────────────────┐  │
│  │  Grok Fast   │  news,tech,     │  Grok Strategy           │  │
│  │ (grok-3-fast)│  social scan    │  (grok-3) brainstorm     │  │
│  └──────┬───────┘                 └──────────┬───────────────┘  │
│         │                                    │ hypotheses       │
│         ▼                                    ▼                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │               Opus CEO  (claude-opus-4)                   │   │
│  │  · Reviews hypotheses (approve / reject)                  │   │
│  │  · Calls MCP tools (trade, portfolio, candles, risk)      │   │
│  │  · Writes self-correction reviews                         │   │
│  │  · Responds to user chat                                  │   │
│  └──────────────────────┬───────────────────────────────────┘   │
│                         │                                        │
│  Loop 3 ─ Watchdog      │      Loop 4 ─ Housekeeping            │
│  · Price spikes ±2.5%   │      · Dynamic universe refresh (4h)  │
│  · Volume surges 3×     │      · Auto trade journal (daily)     │
│  · Sentiment drops 40%  │      · DB backup (daily)              │
│  · Position drawdown    │      · AI cost budget tracking        │
│                         │                                        │
│  ┌──────────────────────▼───────────────────────────────────┐   │
│  │                  MCP SERVER                               │   │
│  │  Tools: get_ai_insights · perform_trade · get_portfolio   │   │
│  │         get_risk_status · get_candles · get_ai_cost       │   │
│  │  Resources: market_data · logs · history                  │   │
│  └──────────────────────┬───────────────────────────────────┘   │
│                         │                                        │
│  ┌──────────────────────▼───────────────────────────────────┐   │
│  │          TRADING ENGINE (Alpaca Markets)                   │   │
│  │  · Multi-timeframe candles (1m–1mo)                       │   │
│  │  · Confidence-based position sizing                       │   │
│  │  · Risk-checked execution                                 │   │
│  └──────────────────────┬───────────────────────────────────┘   │
│                         │                                        │
│  ┌──────────────────────▼───────────────────────────────────┐   │
│  │              RISK MANAGER                                 │   │
│  │  · Balance / slippage / position limits                   │   │
│  │  · Market fatigue detection                               │   │
│  │  · Rest mode (24h pause after consecutive stops)          │   │
│  │  · VIX panic threshold                                    │   │
│  └──────────────────────────────────────────────────────────┘   │
└──────────────────────────┬──────────────────────────────────────┘
                           │ REST + SSE
┌──────────────────────────▼──────────────────────────────────────┐
│              DESKTOP CLIENT  (customtkinter)                     │
│  ┌──────────┐ ┌─────────────────────────────────────────────┐   │
│  │ Sidebar  │ │  Tabs: Overview · AI Strategy · AI Chat     │   │
│  │ Controls │ │        Journal · Full Logs                  │   │
│  │ Portfolio│ │  Chart: multi-timeframe (1m → 1mo)          │   │
│  │ Tickers  │ │  Feed: real-time AI data stream             │   │
│  │ Universe │ │  Chat: direct conversation with Opus CEO    │   │
│  └──────────┘ └─────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## ✨ Features

### 🤖 AI & Strategy
| Feature | Description |
|---------|-------------|
| **3-Model Architecture** | Grok Fast (data) → Grok Strategy (hypotheses) → Opus CEO (decisions) |
| **Dynamic Ticker Universe** | AI-driven symbol selection refreshed every 4 hours |
| **Confidence Scoring** | Each hypothesis rated 0.0–1.0, sizing adjusted accordingly |
| **Social Noise Filter** | Low-reliability sources weighted at 20% to reduce noise |
| **Prompt Injection Defense** | User chat inputs are sanitized against injection patterns |
| **AI Chat** | Direct conversation with Opus CEO from the desktop client |
| **AI Streaming Logging** | Real-time "🧠 Thinking…" display with streamed reasoning in data feed |
| **Daily AI Budget** | Hard cap on daily AI spend ($50 default), tracked per-call |

### 📊 Trading & Risk
| Feature | Description |
|---------|-------------|
| **Multi-Timeframe Candles** | 1min, 5min, 15min, 1h, 1d, 1w, 1mo, 1y with pandas resampling |
| **WebSocket Streaming** | Real-time price updates via Alpaca WebSocket (IEX/SIP) |
| **Intelligent Caching** | Snapshot cache (30s TTL) + candle cache (2min TTL) for reduced API calls |
| **Confidence-Based Sizing** | High confidence → larger positions, low → smaller |
| **Market Fatigue / Rest Mode** | 24h auto-pause after consecutive stop-losses |
| **VIX Panic Threshold** | Auto risk reduction when VIX > 30 |
| **Emergency Kill Switch** | Instantly liquidate all positions with confirmation dialog |
| **Urgency Watchdog** | Real-time alerts for price spikes, volume surges, sentiment drops |

### 🗃 Data & Operations
| Feature | Description |
|---------|-------------|
| **Auto Trade Journal** | Daily AI-generated summaries with wins/losses/lessons |
| **Database Backup** | Automated daily SQLite backups (keeps latest 7) |
| **Log Sanitization** | API keys and sensitive data stripped from all log output |
| **Structured Logging** | JSON logs via structlog with sensitive data redaction |
| **Timezone Awareness** | Configurable display timezone (default: Asia/Seoul) |

### 🖥 Desktop Client
| Feature | Description |
|---------|-------------|
| **6-Tab Interface** | Overview, AI Strategy, AI Chat, Journal, Full Logs, Settings |
| **Live Ticker Cards** | Symbol + price + change% + signal badge in sidebar |
| **Multi-Timeframe Charts** | One-click timeframe switching with server-side candle data |
| **Chart Search Bar** | Autocomplete ticker search above chart |
| **Confidence Gauge** | Visual confidence indicator on strategy tab |
| **AI Cost Badge** | Real-time daily AI spend displayed in top bar |
| **Rest Mode Indicator** | Visual warning when bot is in fatigue rest mode |
| **Universe Panel** | Horizontal scrollable buttons with delta updates + click-to-chart |
| **Settings Tab** | Full runtime configuration with hybrid API key management |
| **Tag-Based Tickers** | Visual tag blocks with live validation for fixed ticker input |
| **Input Validation** | Number-only fields, K/M/B/T auto-formatting, read-only dropdowns |
| **Focus Highlighting** | Active field labels brighten for visual feedback |

---

## 📁 Project Structure

```
Project-Scolecite/
├── server/
│   ├── main.py                  # FastAPI entry point
│   ├── database.py              # SQLAlchemy async (SQLite / PostgreSQL)
│   ├── routers/
│   │   └── api.py               # REST + SSE endpoints (25+ routes)
│   ├── core/
│   │   ├── orchestrator.py      # 4-loop AI controller
│   │   ├── mcp_server.py        # MCP tools & resources for Opus
│   │   ├── trading_engine.py    # Alpaca broker + multi-timeframe candles + caching
│   │   ├── risk_manager.py      # Pre-trade checks + market fatigue
│   │   ├── ai_clients.py        # GrokFast + GrokStrategy + Opus CEO (streaming)
│   │   └── ws_streamer.py       # Real-time Alpaca WebSocket price streaming
│   └── utils/
│       └── logging.py           # Sanitized structured JSON logging + thought broadcast
├── client/
│   └── main.py                  # customtkinter UI — 6-tab terminal
├── shared/
│   ├── config.py                # Pydantic Settings (50+ config fields)
│   └── schemas.py               # Pydantic models (shared DTOs)
├── start_server.bat             # Windows: launch server
├── start_client.bat             # Windows: launch client UI
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

---

## 🚀 Quick Start

### 1. Clone & Install

```bash
git clone <repo-url>
cd Project-Scolecite
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

> **📝 Windows note:** If pip fails building native wheels, add `--only-binary=:all:` to the command.

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your API keys
```

### 3. Start the Server

```bash
# Windows (double-click)
start_server.bat

# Or manually
python -m uvicorn server.main:app --host 0.0.0.0 --port 8000 --reload
```

### 4. Start the Desktop Client

```bash
# Windows (double-click)
start_client.bat

# Or manually
python -m client.main
```

### 5. Use the Dashboard

1. Client auto-connects to `http://localhost:8000`
2. Click **▶ Start** to begin the AI loop
3. Watch real-time data flow into chart and feed panels
4. Switch tabs: **AI Chat** to talk to Opus, **Journal** for daily summaries
5. Use **⚠ EMERGENCY KILL SWITCH** if needed (type `CONFIRM`)

---

## 🔑 Environment Variables

### Required

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Anthropic API key (Claude Opus CEO) |
| `XAI_GROK_API_KEY` | xAI API key (Grok Fast + Grok Strategy) |
| `APCA_API_KEY_ID` | Alpaca API key ID |
| `APCA_API_SECRET_KEY` | Alpaca API secret key |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `POLYGON_API_KEY` | — | Polygon.io API key for enhanced data |
| `APCA_API_BASE_URL` | `https://paper-api.alpaca.markets` | Alpaca endpoint |
| `TRADING_MODE` | `paper` | `paper` or `live` |
| `DATABASE_URL` | `sqlite+aiosqlite:///./scolecite.db` | Database connection string |
| `MAX_POSITION_PERCENT` | `5.0` | Max single position as % of equity |
| `MAX_DRAWDOWN_PERCENT` | `8.0` | Max drawdown before full liquidation |
| `DAILY_LOSS_LIMIT_PERCENT` | `3.0` | Max daily loss before auto-pause |
| `STRATEGY_UPDATE_INTERVAL_MIN` | `180` | Minutes between Opus strategy cycles |
| `GROK_SCAN_INTERVAL_MIN` | `10` | Minutes between Grok Fast scans |
| `DAILY_AI_BUDGET_USD` | `50.0` | Maximum daily AI API cost |
| `CONSECUTIVE_STOP_LOSS_PAUSE` | `3` | Stop-losses before rest mode |
| `VIX_PANIC_THRESHOLD` | `30.0` | VIX level triggering risk reduction |
| `DYNAMIC_UNIVERSE_SIZE` | `10` | Number of tickers in dynamic universe |
| `HIGH_CONFIDENCE_THRESHOLD` | `0.90` | Confidence threshold for larger positions |
| `LOW_CONFIDENCE_THRESHOLD` | `0.60` | Confidence threshold for smaller positions |
| `DISPLAY_TIMEZONE` | `Asia/Seoul` | Timezone for journal & display |
| `DB_BACKUP_ENABLED` | `true` | Enable daily SQLite backups |
| `SOCIAL_NOISE_FILTER_ENABLED` | `true` | Filter low-reliability social sources |
| `LOW_RELIABILITY_WEIGHT` | `0.2` | Weight for noisy social data |
| `ALLOW_EXTENDED_HOURS` | `false` | Enable pre/after-market trading |
| `ENABLE_PROMPT_CACHING` | `true` | Enable Anthropic prompt caching |

---

## 🤖 AI Model Roles

### Grok Fast — `grok-3-fast` via xAI

> **Role:** High-speed data scanner

- Summarises breaking news per symbol
- Runs technical indicator analysis
- Analyses social media sentiment (with noise filter)
- Selects dynamic ticker universe candidates

### Grok Strategy — `grok-3` via xAI

> **Role:** Hypothesis generator

- Receives compiled market data from Grok Fast
- Brainstorms structured trading hypotheses
- Assigns confidence scores (0.0–1.0) to each hypothesis
- Outputs `StrategyHypothesis` objects for Opus review

### Opus CEO — `claude-opus-4` via Anthropic

> **Role:** Chief Executive Officer — final decision maker

- Reviews all hypotheses (approve / reject with reasoning)
- Calls MCP tools to execute trades, fetch data, check risk
- Writes self-correction reviews after each strategy cycle
- Responds to user chat queries about strategy and positions
- Tracks and enforces daily AI budget

---

## 🛡 Risk Management

### Pre-Trade Checks

| Check | Threshold | Action |
|-------|-----------|--------|
| Balance Check | Sufficient buying power | Reject order |
| Slippage Guard | ±0.5% from market price | Reject order |
| Position Size | MAX_POSITION_PERCENT% of equity | Reject order |
| Daily Loss Limit | -DAILY_LOSS_LIMIT_PERCENT% | Pause bot |
| Max Drawdown | -MAX_DRAWDOWN_PERCENT% | Liquidate all + pause |
| **Rest Mode** | Consecutive stop-losses ≥ 3 | **24h auto-pause** |
| **VIX Panic** | VIX > 30 | Reduce risk exposure |
| **AI Budget** | Daily cost > $50 | Block new AI calls |

### Confidence-Based Position Sizing

| Confidence Level | Multiplier | Effect |
|-----------------|------------|--------|
| ≥ 0.90 (high) | 1.5× | Larger position |
| 0.60–0.89 | 1.0× | Standard position |
| < 0.60 (low) | 0.5× | Reduced position |

### Urgency Watchdog Triggers

The watchdog loop runs every 60 seconds and forces an immediate Opus review when:

- **Price Action** — ±2.5% swing in 5 minutes or 3× volume spike
- **Sentiment Shock** — 40%+ sentiment score drop in 10 minutes
- **Sector Meltdown** — QQQ / sector ETF down beyond threshold
- **Position Drawdown** — Any individual position down 2.5%+ from peak

---

## 🔧 MCP Tools

Opus CEO calls MCP tools **proactively** during strategy planning:

### Tools

| Tool | Description |
|------|-------------|
| `get_ai_insights` | Invoke Grok Fast for fresh news/tech/social analysis |
| `perform_trade` | Execute a buy/sell order (risk-checked, confidence-sized) |
| `get_portfolio_status` | Current positions, P&L, equity breakdown |
| `get_risk_status` | Market fatigue state, rest mode, consecutive losses |
| `get_ai_cost_summary` | Daily AI spend, remaining budget |
| `get_candles` | Multi-timeframe OHLCV candle data |

### Resources

| URI | Description |
|-----|-------------|
| `mcp://trading/market_data` | Latest price snapshots for all symbols |
| `mcp://trading/logs` | AI thought-process logs |
| `mcp://trading/history` | Trade history + self-correction reviews |

---

## 📡 API Reference

### Core

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check |
| `GET` | `/api/status` | Full dashboard status (with rest mode, cost, ticker cards) |
| `GET` | `/api/stream` | SSE event stream |

### Bot Control

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/bot/start` | Start AI loop |
| `POST` | `/api/bot/stop` | Stop AI loop |
| `POST` | `/api/bot/emergency` | Emergency kill switch |

### Trading & Portfolio

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/portfolio` | Current portfolio |
| `GET` | `/api/trades` | Trade history |
| `GET` | `/api/strategy` | Current strategy + hypothesis stats |
| `GET` | `/api/insights` | Latest AI insights |
| `GET` | `/api/reviews` | Self-correction reviews |
| `GET` | `/api/candles` | Multi-timeframe candle data |
| `GET` | `/api/risk-status` | Risk manager state |

### AI & Universe

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/chat` | Chat with Opus CEO (sanitized input) |
| `GET` | `/api/universe` | Current dynamic ticker universe |
| `POST` | `/api/universe` | Update ticker universe |
| `GET` | `/api/cost` | AI cost summary |
| `GET` | `/api/tickers/search` | Autocomplete ticker search |
| `GET` | `/api/ticker-cards` | Live ticker card data |
| `GET` | `/api/journal` | Auto-generated trade journal entries |
| `GET` | `/api/settings` | Current runtime settings |
| `PATCH` | `/api/settings` | Update settings (partial patch) |
| `POST` | `/api/settings/reset` | Reset settings to defaults |
| `GET` | `/api/settings/keys/status` | API key configuration status |
| `POST` | `/api/settings/keys` | Submit API keys to server |

### MCP

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/mcp/tools` | List available MCP tools |
| `GET` | `/mcp/resources` | List available MCP resources |
| `POST` | `/mcp/call` | Call an MCP tool |
| `POST` | `/mcp/read` | Read an MCP resource |

---

## 🖥 Desktop Client

The customtkinter desktop client features a **dark navy professional theme** with 5 tabs:

| Tab | Contents |
|-----|----------|
| **Overview** | Real-time multi-timeframe chart (1m–1y selector), AI data feed with streaming reasoning |
| **AI Strategy** | Opus strategy display, confidence gauge, hypothesis accept/reject stats, self-correction log |
| **AI Chat** | Direct conversation with Opus CEO — ask about strategy, positions, market conditions |
| **Journal** | Daily AI-generated trade journals with wins, losses, P&L, lessons learned |
| **Full Logs** | System log stream with syntax-highlighted levels (info/warn/error/strategy) |
| **Settings** | Full runtime config: risk, AI models, tickers, chart, timezone, API keys |

### Sidebar

- **Connection Status** — LED indicators for Server, Claude, Grok, Alpaca
- **Bot Controls** — Start / Stop / Emergency Kill Switch
- **Portfolio** — Equity, cash, daily P&L, total P&L, positions count
- **Ticker Cards** — Live symbol cards with price, change%, signal badge (clickable for chart)
- **Universe** — Horizontal scrollable ticker buttons with delta updates

### Top Bar

- AI cost badge (daily spend)
- Paper/Live mode indicator
- Rest mode warning (💤)
- Pulse animation when bot is running
- UTC clock

---

## ☁ Cloud Run Production Deployment

> **Cloud Run 프로덕션 배포 가이드** — 전체 인프라 자동화 스크립트 포함

### Prerequisites / 사전 준비

| Tool | Version | Install |
|------|---------|---------|
| `gcloud` CLI | latest | [Install](https://cloud.google.com/sdk/docs/install) |
| Docker | 20+ | [Install](https://docs.docker.com/get-docker/) |
| Terraform (선택) | 1.5+ | [Install](https://developer.hashicorp.com/terraform/install) |

```bash
# Authenticate / 인증
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
```

---

### Architecture / 아키텍처

```
┌────────────────────────────────────────────────────────────────┐
│                    Google Cloud Platform                        │
│                                                                │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐  │
│  │  Cloud Run    │───▶│  Cloud SQL   │    │ Secret Manager   │  │
│  │  scolecite-bot│    │  PostgreSQL  │    │ API Keys / DB URL│  │
│  │  (gen2, 2 CPU)│    │  (db-f1-micro)│    │                  │  │
│  │  min=0 max=3  │    │              │    │                  │  │
│  └───────┬───────┘    └──────────────┘    └──────────────────┘  │
│          │                    ▲                      ▲          │
│          │  unix socket       │   env injection      │          │
│          └────────────────────┘──────────────────────┘          │
│                                                                │
│  ┌──────────────┐    ┌──────────────┐                          │
│  │ VPC Connector │    │ Artifact     │                          │
│  │ 10.8.0.0/28  │    │ Registry     │                          │
│  └──────────────┘    └──────────────┘                          │
└────────────────────────────────────────────────────────────────┘
          ▲
          │ HTTPS (SSE streaming)
          │
┌─────────┴─────────┐
│  Desktop Client    │
│  customtkinter     │
└───────────────────┘
```

---

### Option A: One-Click Deploy Script / 원클릭 배포

```bash
# 모든 인프라 + 배포를 한번에 실행
export GCP_PROJECT_ID=your-project-id
bash deploy/deploy.sh
```

The script automatically: / 스크립트가 자동으로:
1. Enables required GCP APIs / 필요한 API 활성화
2. Creates Artifact Registry / 컨테이너 레지스트리 생성
3. Creates Cloud SQL (PostgreSQL 15) / DB 인스턴스 생성
4. Sets up Secret Manager / 시크릿 매니저 설정
5. Creates VPC Connector / VPC 커넥터 생성
6. Builds & pushes Docker image / 이미지 빌드·푸시
7. Deploys to Cloud Run / Cloud Run 배포

---

### Option B: Cloud Build CI/CD / CI/CD 파이프라인

```bash
# Submit a build manually / 수동 빌드 제출
gcloud builds submit . \
  --config=cloudbuild.yaml \
  --substitutions=_CLOUD_SQL_CONN=PROJECT:asia-northeast3:scolecite-db

# Or set up a trigger on push to main / main 푸시 트리거 설정
gcloud builds triggers create github \
  --name=scolecite-deploy \
  --repo-name=scolecite \
  --repo-owner=YOUR_GITHUB_USER \
  --branch-pattern='^main$' \
  --build-config=cloudbuild.yaml \
  --substitutions=_CLOUD_SQL_CONN=PROJECT:asia-northeast3:scolecite-db
```

---

### Option C: Terraform / 테라폼 (IaC)

```bash
cd deploy/terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your project ID

terraform init
terraform plan -var="db_password=$(openssl rand -base64 24)"
terraform apply -var="db_password=$(openssl rand -base64 24)"
```

---

### Cloud SQL Setup / Cloud SQL 설정

| Setting | Value | 설명 |
|---------|-------|------|
| Engine | PostgreSQL 15 | 최신 안정 버전 |
| Tier | `db-f1-micro` | 개발/소규모 ($7.67/mo) |
| Region | `asia-northeast3` (Seoul) | 한국 리전 |
| Storage | 10GB auto-increase | 자동 증설 |
| Backup | Daily 04:00 UTC | 자동 백업 |

**Connection method / 연결 방식:**
- Cloud Run → Cloud SQL: **Unix socket** via `/cloudsql/PROJECT:REGION:INSTANCE`
- No Cloud SQL Auth Proxy needed in Cloud Run (built-in)
- `DATABASE_URL` format:
  ```
  postgresql+asyncpg://scolecite:PASSWORD@/scolecite?host=/cloudsql/PROJECT:REGION:scolecite-db
  ```

---

### Secret Manager / 시크릿 관리

All API keys are stored in **Secret Manager** (never in env vars or `.env`):

| Secret Name | Description | 설명 |
|-------------|-------------|------|
| `ANTHROPIC_API_KEY` | Claude API key | Anthropic API 키 |
| `XAI_GROK_API_KEY` | Grok API key | xAI Grok API 키 |
| `POLYGON_API_KEY` | Market data API key | 시세 데이터 API 키 |
| `APCA_API_KEY_ID` | Alpaca key ID | 브로커 API Key |
| `APCA_API_SECRET_KEY` | Alpaca secret key | 브로커 Secret Key |
| `DATABASE_URL` | PostgreSQL connection string | DB 접속 문자열 |

```bash
# Set a secret value / 시크릿 값 설정
echo -n 'sk-ant-...' | gcloud secrets versions add ANTHROPIC_API_KEY --data-file=-

# Verify / 확인
gcloud secrets versions access latest --secret=ANTHROPIC_API_KEY
```

Cloud Run automatically injects these as environment variables → `pydantic-settings` reads them natively.
Cloud Run이 환경변수로 자동 주입 → `pydantic-settings`가 자동 인식합니다.

---

### Service Configuration / 서비스 설정

| Parameter | Value | 설명 |
|-----------|-------|------|
| `region` | `asia-northeast3` | 서울 리전 (한국 최저 지연) |
| `min-instances` | `0` | 비용 절약 (cold start 허용) |
| `max-instances` | `3` | 최대 인스턴스 수 제한 |
| `concurrency` | `80` | 인스턴스당 동시 요청 수 |
| `cpu` | `2` | AI 처리용 2 vCPU |
| `memory` | `2Gi` | 모델 응답 처리 + pandas |
| `timeout` | `300s` | AI 응답 대기 (최대 5분) |
| `execution-environment` | `gen2` | Cloud Run gen2 (gVisor) |
| `startup-cpu-boost` | `true` | Cold start 시 CPU 부스트 |
| `vpc-egress` | `all-traffic` | VPC 경유 외부 트래픽 |

---

### Health Checks & Probes / 헬스체크

| Probe | Endpoint | Purpose | 설명 |
|-------|----------|---------|------|
| **Startup** | `GET /health` | Container ready? | 컨테이너 시작 확인 |
| **Liveness** | `GET /health` | Still alive? | 프로세스 생존 확인 |
| **Readiness** | `GET /ready` | DB connected? | DB 연결 상태 확인 |

```bash
# Test health / 헬스체크 테스트
curl https://YOUR_SERVICE_URL/health
# → {"status": "ok", "mode": "paper"}

curl https://YOUR_SERVICE_URL/ready
# → {"status": "ready", "db": "connected"}
```

---

### Environment Variable Management / 환경변수 관리

```
┌─────────────────────────────────────────────────────────┐
│              Environment Variable Sources                │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  1. Secret Manager (API keys, DATABASE_URL)             │
│     → Cloud Run injects as env vars at startup          │
│     → 시크릿 매니저에서 시작 시 자동 주입               │
│                                                         │
│  2. Cloud Run env vars (TRADING_MODE, etc.)             │
│     → Set in deploy command or cloudbuild.yaml          │
│     → 배포 명령어나 cloudbuild.yaml에서 설정            │
│                                                         │
│  3. settings.json (runtime-tunable parameters)          │
│     → Modified via Settings tab → PUT /api/settings     │
│     → Settings 탭에서 실시간 변경 가능                  │
│     → Persisted in container filesystem (ephemeral)     │
│     → 컨테이너 재시작 시 초기화됨 (필요시 GCS 연동)     │
│                                                         │
│  Priority: Secret Manager > env vars > settings.json    │
│           > .env (local dev only)                       │
└─────────────────────────────────────────────────────────┘
```

---

### Cost Optimization / 비용 최적화

| Strategy | Impact | 설명 |
|----------|--------|------|
| `min-instances=0` | 💰💰💰 | 미사용 시 과금 없음 (Cold start ~2-3s) |
| `startup-cpu-boost` | ⚡ | Cold start 시 CPU 부스트로 시작 시간 단축 |
| `ENABLE_PROMPT_CACHING=true` | 💰💰 | Anthropic 프롬프트 캐싱 (최대 90% 비용 절감) |
| `db-f1-micro` tier | 💰💰 | 최소 DB 티어 (~$7.67/mo) |
| `e2-micro` VPC connector | 💰 | 최소 VPC 티어 |
| Gen2 execution environment | ⚡ | 더 빠른 cold start |
| Multi-stage Docker build | ⚡ | 작은 이미지 → 빠른 배포 |
| `pool_pre_ping=True` | 🛡️ | DB 연결 재활용, 끊김 방지 |
| Gunicorn 2 workers | ⚡ | 효율적인 동시성 처리 |
| Artifact Registry (not GCR) | 💰 | 최신 권장, 비용 효율적 |

**Estimated monthly cost (idle) / 예상 월 비용 (대기 시):**
- Cloud SQL db-f1-micro: ~$7.67
- VPC Connector (2× e2-micro): ~$14
- Cloud Run (min=0): $0 when idle
- **Total idle: ~$22/mo**

**Estimated monthly cost (active trading) / 예상 월 비용 (활성 트레이딩):**
- Cloud Run: ~$5-15 (depending on usage)
- AI API costs: varies (tracked in-app via `/api/cost`)
- **Total active: ~$40-55/mo** (excluding AI API)

---

### Post-Deployment Checklist / 배포 후 체크리스트

```
 1. ✅ Health check 확인
    curl https://SERVICE_URL/health

 2. ✅ Readiness check 확인 (DB 연결)
    curl https://SERVICE_URL/ready

 3. ✅ API status 확인
    curl https://SERVICE_URL/api/status

 4. ✅ Secret Manager에 모든 API 키 설정
    gcloud secrets versions list ANTHROPIC_API_KEY
    gcloud secrets versions list XAI_GROK_API_KEY
    gcloud secrets versions list APCA_API_KEY_ID

 5. ✅ Desktop client 연결 테스트
    → Settings에서 Server URL을 Cloud Run URL로 변경

 6. ✅ SSE 스트리밍 테스트
    curl -N https://SERVICE_URL/api/stream

 7. ✅ Cloud Run 로그 확인
    gcloud run services logs read scolecite-bot --region=asia-northeast3

 8. ✅ Paper 모드로 봇 시작
    curl -X POST https://SERVICE_URL/api/bot/start

 9. ✅ AI 비용 모니터링
    curl https://SERVICE_URL/api/cost

10. ✅ Cloud SQL 백업 확인
    gcloud sql backups list --instance=scolecite-db
```

---

### Useful Commands / 유용한 명령어

```bash
# View logs / 로그 보기
gcloud run services logs read scolecite-bot --region=asia-northeast3 --limit=100

# Update env var / 환경변수 수정
gcloud run services update scolecite-bot \
  --region=asia-northeast3 \
  --update-env-vars="TRADING_MODE=live"

# Scale to 0 (cost save) / 비용 절약 스케일다운
gcloud run services update scolecite-bot \
  --region=asia-northeast3 \
  --min-instances=0

# Force new revision / 강제 새 리비전
gcloud run deploy scolecite-bot \
  --region=asia-northeast3 \
  --image=asia-northeast3-docker.pkg.dev/PROJECT/scolecite/scolecite-bot:latest

# Connect to Cloud SQL (debug) / 디버그용 DB 접속
gcloud sql connect scolecite-db --user=scolecite --database=scolecite

# Delete service (cleanup) / 서비스 삭제
gcloud run services delete scolecite-bot --region=asia-northeast3
```

---

## 🧰 Tech Stack

| Layer | Technology |
|-------|-----------|
| **Server** | FastAPI · SQLAlchemy (async) · SSE-Starlette · Pydantic v2 · WebSockets |
| **Client** | customtkinter · matplotlib · httpx |
| **AI** | Claude Opus 4 (CEO, streaming) · Grok 3 (Strategy, streaming) · Grok 3 Fast (Data, streaming) |
| **Broker** | Alpaca Markets (paper + live) |
| **Database** | SQLite (dev) / Cloud SQL PostgreSQL (prod) · aiosqlite · asyncpg |
| **Analysis** | pandas · numpy · ta (technical analysis) |
| **Resilience** | tenacity (retry) · structlog (sanitized logging) |
| **Deployment** | Docker · Google Cloud Run (gen2) · Cloud Build · Terraform · Artifact Registry |

---

## 📄 License

MIT

---

<p align="center">
  <sub>Built with ◆ for research. Trade responsibly.</sub>
</p>
