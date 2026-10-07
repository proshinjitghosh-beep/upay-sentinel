# Upay Sentinel 2.0
🌐 **Live Demo:** https://upay-sentinel.onrender.com/

Synthetic fraud investigation prototype for Track 01: Trust & Risk Intelligence.

## Start

Python 3.10+ is required. First installation needs internet for Python dependencies. Once installed, the UI, synthetic data, graph and map do not need internet.

**Windows:** Extract this ZIP, then double-click `START_WINDOWS.bat`. Open http://127.0.0.1:8000 after startup. The Python fallback engine runs on Windows; no GCC needed.

**Linux/macOS:** Run `./start.sh`, then open http://127.0.0.1:8000. GCC is optional and compiles the C engine when available.

**Manual setup:**

```bash
python -m venv .venv
# Activate: Windows .venv\Scripts\activate | Linux/macOS source .venv/bin/activate
python -m pip install -r requirements.txt
python -m data.generate_data
python -m uvicorn backend.app.main:app --port 8000
```

The ZIP includes generated CSV data. Regeneration is optional. Keep the server running; opening `frontend/index.html` directly cannot access the API.

## Version 2 features

- Offline dashboard written in plain JavaScript, with no React/Babel/font/CDN dependency.
- Sidebar, persistent dark mode, responsive screens, wallet/region search, HOLD/WATCH filters.
- Overview, Alert Queue, Money Trail, Live Monitor, Scam Simulator, Customer View, Audit Log, Evidence.
- SQLite audit events: analyst, timestamp, notes, outcome labels and risk/evidence snapshot.
- Case identities change when wallet transactions or scoring evidence change. Release is single-use for one subsequent simulated cash-out check on unchanged case evidence. Confirm hold or escalate revokes an unused release. Changes survive restarts on persistent disk.
- Weighted risk contributions: graph /40, anomaly /40, rule /20. These are score points, not calibrated fraud probabilities or feature-level SHAP explanations.
- Money-trail timeline; interactive graph and offline geographic area schematic; customer division/city centroids; synthetic agent areas including Mirpur/Uttara/Motijheel examples.
- Agent marker inspection and flagged cash-out regional hotspots. Association is not proof of agent wrongdoing.
- `UNUSUAL_CASHOUT_ROUTE` supporting evidence compares traced cash-out regions with that wallet's history before the inflow. No GPS, no impossible-travel inference, no location weight added to the score.
- Fast scam, evasive scam, legitimate transfer and chronological dataset-suffix replay scenarios. Manual step and auto play. Independent replay scope; reset creates a new run without deleting old audit history.
- Early rapid-split graph signal (`PRE_CASHOUT_FAN_OUT`) raises graph risk before completed cash-outs when a large inflow reaches at least three accounts rapidly. A separate cash-out policy pauses a review-band wallet with a recent meaningful inflow from a currently high-risk hub (`RECENT_HIGH_RISK_UPSTREAM`), unless a one-use analyst release exists. This policy is separate from the model score and is returned in cash-out results.
- Frozen Isolation Forest trained on the first 70% of transactions in time. Replay features use observed transactions only. Cash-out gate is checked before a cash-out event is appended.
- Customer warning preview, in English and Bangla, for risky recipients or large first-time-recipient transfers. Review/Cancel flow; no money is sent.
- JSON case evidence and audit exports. Analyst outcome labels are stored for future validation, not automatic online retraining.
- Positive, finite demo cash-out amounts up to Tk 1,000,000; bounded strings and API inputs; optional API key on mutations.

## Demo walkthrough

1. Open Overview and inspect top batch alerts, regional hotspots and Evidence.
2. Open Alert Queue, select a wallet, compare Graph/Map, inspect an agent marker and the timeline.
3. Enter analyst name/note/outcome. Confirm a hold; check cash-out. Release once; check again. A further check is held again if risk remains high. Export the case evidence.
4. Open Scam Simulator, select Fast mule ring and press Reset / New run. Step through transfers or Auto play. Inspect focus contributions, rule-vs-model output and cash-out request metrics.
5. Compare the Evasive and Legitimate scenarios. Reset clears only the current replay state; past audit runs remain in SQLite.
6. Open Customer View and review a large transfer to a risky/new recipient; cancel it.
7. Open Audit Log to view/export current-scope history.

Live Monitor uses a local synthetic replay, not a production transaction feed. Batch and replay scopes are selectable at the top. Replay state/model lives in memory; restarting starts a new run. Audit records remain on disk.

## Evidence and honest limitations

Batch evidence retains the original 70/30 account holdout and is a **retrospective synthetic comparison**. It is not an isolated fraud-ring holdout: graph edges may cross account splits. The original fraud cash-out fraction scored high using the completed ledger is not money prevented or recovered.

Replay trains on an earlier chronological prefix and updates only from observed events. It evaluates each cash-out request using prior evidence. Ledger events are then appended even if the parallel gate recommends pause: this is a **counterfactual gate comparison**, not a real blocked ledger. Subsequent observations may produce alerts even though the first cash-out was missed. Metrics show observed fraud cash-out requests, amount that the prior-evidence gate would pause, legitimate requests paused, and first focus-review delay. No promise that every scenario is caught early.

The synthetic account labels are used for evaluation, not scoring. Replay evaluations on a tiny scripted sample do not establish accuracy. Original generator cash-out agent choices are random; location history is illustrative and not a validated geography model. New/established account fairness gaps reflect synthetic generation assumptions. City/agent coordinates and the SVG outline are approximate. Customers have no precise personal locations.

Graph traversal remains a time-respecting heuristic from each wallet's largest inflow; it does not perform balance-conserving forensic attribution. Value reaching an agent can include other money and should not be described as recovered victim funds. The fast rule's pass-through ratio is aggregate ledger behaviour, not exact flow attribution. Score weights are hand-set and need governed validation.

Face verification is a simulated **request**, not an implemented biometric check. No transfers, holds, customer authentication or real-world financial decisions occur. Analyst identity is a demo text field, not authenticated identity. Outcome labels require human review before use in retraining. This is a single-worker prototype; large-scale ingestion, drift monitoring, immutable/tamper-evident audits, role-based access, expiring holds and production authentication remain future work.

## Security / configuration

```bash
# Optional mutation protection; set in your terminal before starting:
# Linux/macOS: export SENTINEL_API_KEY='your-secret'
# Windows CMD: set SENTINEL_API_KEY=your-secret
```

For the demo, open `/?key=your-secret`. URL keys can appear in history/logs; use a proper authenticated session for production. Read endpoints expose synthetic-only evidence and are not authenticated. `ALLOWED_ORIGINS` defaults to localhost/127.0.0.1 on port 8000. `SENTINEL_DB` selects the SQLite path; default `data/sentinel.sqlite3`. Set a durable writable path on hosted deployments. Ephemeral hosting disks do not preserve the database across redeployments. Launch with one worker; replay state is not shared across workers. The app does not automatically load `.env`; set environment variables explicitly or use Uvicorn's env-file option with its required dependency.

No external map tile or AI API service is used. First-time pip installation uses external package repositories. The frontend can use `?api=https://your-api` when hosted separately with matching CORS.

## Tests

```bash
python -m pytest -q backend/tests
```

Eleven tests cover engine/fallback agreement, dataset shape, a known mule trail, original synthetic holdout floors, API flow, invalid amounts/IDs, persistent single-use release, changed-evidence invalidation, frozen-model replay and no future cash-out input, API-key enforcement and location/warning fields. Tests use temporary databases for the new product cases.

## APIs

`GET /api/health`, `/api/summary`, `/api/alerts`, `/api/alerts/{id}`, `/api/audit`, `/api/hotspots`, `/api/agents/{id}`, `/api/replay`.

`POST /api/alerts/{id}/decision`, `/api/check-cashout`, `/api/sender-check`, `/api/replay/reset`, `/api/replay/step`.

Read/decision/cash-out APIs accept `?scope=batch` or `?scope=replay` (default batch). Interactive schema: `/docs`. The replay step endpoint ingests the next local queued record, not arbitrary external records.

## Architecture

C / Python time-respecting trace → NetworkX propagation + Isolation Forest + separate rule → FastAPI → analyst action and SQLite audit. The replay trains once on a historical prefix, then reuses its frozen model; it currently recomputes features over the observed ledger per event and is not optimized streaming infrastructure.

Deployment configuration from the original project remains in `render.yaml`. This package has not been pushed or deployed.

## Verified sample outcomes

On the supplied seed, the fast scripted ring pauses Tk 53,700 in three requests using the separate upstream policy; first focus review is at 180 seconds. The evasive scenario pauses zero of Tk 53,700 and only reaches review after the first cash-out (1,300 seconds). The legitimate scripted case pauses zero requests. These are tiny synthetic demonstrations, not production performance.
