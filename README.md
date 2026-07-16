# ShwapnoOps AI — Smart Retail Operations Assistant

A working reference build for the ShwapnoOps AI proposal: an AI assistant that helps
Outlet Managers prioritize tasks, catch stock-out risk early, plan manpower, and get
daily operational recommendations — backed by a FastAPI service built for async,
real-time analytics processing.

## What's included

- **FastAPI backend** (`/backend`) — fully async, typed, auto-documented at `/docs`
- **Task Prioritization Engine** — multi-factor scoring (urgency, business impact,
  severity, recency) that ranks operational tasks 0–100
- **Alert Engine** — async scanners for stock-out risk, manpower shortage, and
  complaint spikes; each scan writes alerts *and* auto-generates prioritized tasks
- **Real-time layer** — WebSocket channel per outlet (+ an "all outlets" channel for
  HQ views) so the dashboard receives new alerts the moment they're detected, and a
  recurring background scheduler that re-scans every outlet on an interval
- **Operational chatbot** — intent-classified, data-grounded Q&A ("what should I
  prioritize today", "which SKUs are at risk", "how's my manpower coverage") with
  Gemini GenAI response composition when `GEMINI_API_KEY` is configured, plus a
  deterministic local fallback for offline demos
- **Gemini category demand forecasting** — sends database-backed category sales,
  inventory, promotions, deliveries, seasonal events, and live weather context to
  Gemini and stores daily category forecasts with confidence, drivers, stock gaps,
  and risk levels
- **Normalized product categories** — `product_categories` is the category master;
  sales, inventory, movements, stock-outs, promotions, deliveries, and forecasts
  reference it through `category_id`
- **Dashboard frontend** (`/frontend/index.html`) — responsive manager workspace
  with scorecards, live task queue, target tracking, sales/footfall trends, weather,
  staffing, AI decision tools, WebSocket alerts, chatbot, and exports
- **Analytics frontend** (`/frontend/analytics.html`) — selectable, database-driven
  graphs with period and revenue/unit controls
- **Data Tables frontend** (`/frontend/data-tables.html`) — focused views for each
  operational dataset instead of crowding all tables onto the dashboard
- **Seed data generator** — realistic multi-outlet demo data (sales, inventory,
  stock movement, stock-out history, manpower, complaints, campaigns, seasonal
  events, deliveries, audits, manual issues, alerts, and prioritized tasks) so the
  whole thing runs out of the box

## Why this architecture supports scalable, async, real-time analytics

1. **Fully async request path.** Every route and DB call uses SQLAlchemy's async
   engine (`asyncio` + `aiosqlite`/`asyncpg`), so the API can serve many concurrent
   outlet dashboards without blocking on I/O.
2. **Decoupled analytics from the request/response cycle.** Alert scans run as
   background jobs (APScheduler in this reference build), not inside user requests.
   Each outlet gets its own DB session and scans run independently, so one slow
   outlet never blocks another.
3. **A documented, drop-in path to horizontal scale.** `app/workers/celery_app.py`
   and `celery_tasks.py` show exactly how to move from the in-process scheduler to
   distributed Celery workers + Celery Beat, sharding scans per-outlet or per-region
   across worker nodes — no changes needed to the scoring/alert logic itself.
4. **Real-time fan-out.** The WebSocket manager is built so it can be swapped for a
   Redis pub/sub-backed broadcaster, letting alerts reach dashboard clients
   connected to *any* API replica once you run more than one instance.
5. **Swap SQLite for Postgres in one line.** `DATABASE_URL` in `app/config.py` is the
   only thing that changes to move from the local demo DB to a production
   PostgreSQL instance (`postgresql+asyncpg://...`).

## Configuration

Copy the example environment file and set local values:

```bash
cp backend/app/.env.example backend/app/.env
```

Important settings:

```bash
GEMINI_API_KEY=your_key_here
GEMINI_MODEL=gemini-3.5-flash
GEMINI_TIMEOUT_SECONDS=18
WEATHER_API_URL=http://your-weather-service/weather
WEATHER_DISTRICT=Dhaka
```

Never commit `backend/app/.env`; it is excluded by `.gitignore`. The Docker setup
loads this file at runtime.

## Run it locally

```bash
cd backend
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m app.seed_data         # optional: destructive demo-data reset
uvicorn app.main:app --reload   # API: http://localhost:8000
```

In another terminal:

```bash
cd frontend
python -m http.server 3000
```

Runtime pages and APIs read operational records from the database. `seed_data.py`
is only an explicit development/reset command; it is not a runtime data source.
Weather is fetched from the configured external weather service.

Supported action API purposes are `prioritize_tasks`, `stock_replenishment`,
`manpower_reallocation`, `complaint_triage`, `daily_brief`, `delivery_risk`,
`campaign_readiness`, `audit_action_plan`, `festival_preparedness`,
`root_cause_analysis`, and `regional_summary`.

The AI context automatically includes local business date/time from
`BUSINESS_TIMEZONE` (default `Asia/Dhaka`) and the next seasonal/festival event
within `FESTIVAL_LOOKAHEAD_DAYS` (default `7`).

## Run with Docker

```bash
docker compose up --build
```
This brings up the API, Redis (ready for the Celery scale-out path), and the static
frontend via nginx.

The Docker API container stores the SQLite database at `/app/data/shwapno_ops.db`,
mounted from `./backend/data/shwapno_ops.db` on your machine, so local database
state persists across container rebuilds.

Dashboard pages:
- Manager dashboard: `http://localhost:3000/`
- Analytics graphs: `http://localhost:3000/analytics.html`
- Operational data tables: `http://localhost:3000/data-tables.html?view=categories`
- API documentation: `http://localhost:8000/docs`

## Key API endpoints

| Purpose | Endpoint |
|---|---|
| List outlets | `GET /api/outlets` |
| Sales trend | `GET /api/sales/trend?outlet_id=1&days=14` |
| Category sales (today/month/year) | `GET /api/sales/by-category?outlet_id=1` |
| Product category master | `GET /api/categories` |
| Product category availability | `GET /api/categories/summary?outlet_id=1` |
| Monthly/weekly/daily sales target progress | `GET /api/targets/progress?outlet_id=1` |
| Inventory + risk levels | `GET /api/inventory?outlet_id=1` |
| Manpower roster / shift optimizer | `GET /api/manpower?outlet_id=1`, `GET /api/manpower/optimize?outlet_id=1` |
| Log a complaint | `POST /api/complaints` |
| Prioritized task queue | `GET /api/tasks?outlet_id=1&status=pending` |
| Gemini reprioritize task queue from live data | `POST /api/tasks/prioritize?outlet_id=1` |
| Trigger an analytics scan manually | `POST /api/alerts/scan?outlet_id=1` |
| Chatbot | `POST /api/chat` |
| Purpose-built AI actions | `POST /api/ai/actions` |
| AI recommendation audit trail | `GET /api/ai/recommendations?outlet_id=1` |
| Approve/reject/escalate AI recommendation | `POST /api/ai/recommendations/{id}/approve`, `/reject`, `/escalate` |
| Gemini category demand forecast | `POST /api/forecasts/demand/run?outlet_id=1&horizon_days=7`, `GET /api/forecasts/demand?outlet_id=1` |
| Inventory movement ledger | `GET /api/operations/inventory-movements?outlet_id=1` |
| Stock-out history | `GET /api/operations/stock-outs?outlet_id=1` |
| Promotion calendar | `GET /api/operations/promotions?outlet_id=1` |
| Delivery schedules | `GET /api/operations/deliveries?outlet_id=1` |
| Seasonal events | `GET /api/operations/seasonal-events?outlet_id=1` |
| Business calendar context | `GET /api/operations/calendar-context?outlet_id=1` |
| 7-day weather demand context | `GET /api/operations/weather-context?outlet_id=1` |
| Store audit reports | `GET /api/operations/audit-reports?outlet_id=1` |
| Manual issue reporting | `GET/POST /api/operations/manual-issues` |
| Outlet scorecard | `GET /api/dashboard/scorecards/1` |
| CSV / PDF export | `GET /api/dashboard/export/tasks.csv`, `/export/summary.pdf` |
| Real-time alert stream | `WS /ws/outlet/{id}`, `WS /ws/all` |

Full interactive API reference: run the server and visit `http://localhost:8000/docs`.

## Data model → proposal mapping

| Proposal input | Where it lives |
|---|---|
| Daily sales by SKU/category | `SalesRecord` |
| Product category master | `ProductCategory`; referenced through `category_id` |
| Monthly/weekly/daily outlet targets | `OutletSalesTarget`, `/api/targets/progress` |
| Inventory current stock | `InventoryItem` |
| Inventory & stock movement data | `InventoryMovement` |
| Stock-out history | `StockOutEvent` |
| Manpower roster & attendance | `ManpowerRoster` |
| Customer complaints/feedback | `Complaint` |
| Promotion & campaign calendar | `PromotionCampaign` |
| Peak-hour footfall | `SalesRecord.footfall`, `ManpowerRoster.peak_hour_footfall_forecast` |
| Delivery schedules | `DeliverySchedule` |
| Outlet operational KPI reports | `ScorecardOut`, dashboard exports, `/api/dashboard/scorecards` |
| Seasonal/festival trends | `SeasonalEvent`, `SalesRecord.is_festival_period` |
| Weather-driven demand forecast | External weather API via `/api/operations/weather-context`, then Gemini action purpose `weather_demand` |
| Gemini category forecasting output | `DemandForecast`, `/api/forecasts/demand/run` |
| Approval/escalation workflow | `AiRecommendationAudit`, `/api/ai/recommendations/{id}/approve`, `/reject`, `/escalate` |
| Historical AI recommendation audit trail | `AiRecommendationAudit`, `/api/ai/recommendations` |
| Store audit reports | `StoreAuditReport` |
| Manual issue reporting by Outlet Managers | `ManualIssue`, `POST /api/operations/manual-issues` |

ERP/SAP exports, scanned audit PDFs, and third-party API feeds can now land into
the dedicated source tables above via scheduled ETL jobs, while the prioritization
and AI action APIs consume the same operational context.

The forecast service aggregates outlet records by product category, combines them
with the configured weather forecast, and asks Gemini for daily category demand.
Gemini output is validated before it is stored. Inventory gap and risk calculations
are then derived from the Gemini-predicted units and current database inventory.
If Gemini is unavailable or returns invalid data, the forecast endpoint returns an
explicit service error; it does not silently substitute a static forecast.

## Honest limitations of this reference build

- The chatbot is grounded to operational API data. Gemini improves wording and
  synthesis when configured, but it intentionally does not invent facts outside
  the retrieved outlet context.
- Auth/RBAC (manager vs. regional vs. HQ views) is not implemented — `SECRET_KEY`
  and JWT dependencies are already in `requirements.txt` as the intended path.
- SMS/push notification delivery is not wired up; the alert engine already emits a
  structured event per alert, so plugging in an SMS/push gateway is additive, not a
  redesign.
- Category forecasting depends on the configured Gemini and weather services, so
  generation latency and availability depend on those external systems.
- SQLite and the in-process scheduler are appropriate for local/reference use;
  production deployments should use PostgreSQL and distributed workers.
