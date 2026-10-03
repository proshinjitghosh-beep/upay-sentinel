# Upay Sentinel — AI-Powered Graph Fraud & Mule Account Detection

Track 01 (Trust & Risk Intelligence), AI Dev Fest 2026 AI Hackathon, DIU CPC × upay.
Live deployment: **<ADD YOUR RENDER URL HERE>**

## Project overview
Scam victims send money themselves with a valid PIN/OTP, so ordinary checks see a normal transfer. Fraudsters then split the money across mule accounts and cash out at agents within minutes.
Sentinel follows the money trail instead of checking one transaction at a time. It answers the three questions in the upay guideline: **what happened, why it is risky, what upay should do next.**

## Features
- **Graph money-trail engine (C):** time-respecting traversal from each account's largest inflow; measures fan-out, hop depth, cash-out value reached and delay.
- **Behavioural anomaly score (AI):** Isolation Forest over account behaviour + graph features, fit on a 70% training split and judged on held-out accounts.
- **Network propagation (NetworkX):** accounts fed by a mule hub inherit risk, so the downstream mules are caught even when each looks ordinary alone.
- **Business rule kept separate from ML:** fast pass-through rule, reported next to the model so you can see what the AI adds.
- **Explainability:** every score shows reason codes and a grounded What / Why / Next narrative (template over structured evidence, no free-form LLM making decisions).
- **Human oversight:** risk ≥85 puts a *temporary* hold on cash-out and asks for live face verification, then queues the case. An analyst confirms, releases or escalates. Nothing is blocked permanently by the model.
- **Dashboard (React):** alert queue, live money-trail graph, analyst actions, cash-out simulator, held-out evidence table and fairness check.

## Technology stack
C (core engine, loaded with `ctypes`) · Python 3.10+ · FastAPI · scikit-learn (Isolation Forest) · NetworkX · pandas/NumPy · Faker (synthetic data) · React 18 (served as one static page, no build step) · pytest · Render.
No external AI APIs or secrets are used.

## Requirements
Python 3.10+, `gcc` (optional: without it the same algorithm runs in pure Python, shown as "Python fallback engine" in the header), internet access for the CDN scripts used by the dashboard.

## Installation and setup
```bash
git clone <your-repo-url> && cd upay-sentinel
./build.sh          # installs deps, generates data, compiles the C engine
```
Manual equivalent: `pip install -r requirements.txt && python -m data.generate_data && gcc -O2 -shared -fPIC -o engine/libsentinel.so engine/graph_engine.c`

## Environment variables
| Name | Purpose |
|---|---|
| `SENTINEL_API_KEY` | Optional. If set, POST endpoints require header `X-API-Key`. Open the dashboard as `/?key=YOUR_KEY`. |
| `ALLOWED_ORIGINS` | Comma-separated CORS origins (default `*`). If the dashboard is hosted elsewhere, open it as `/?api=https://your-api-url`. |

Copy `.env.example`; never commit real keys.

## Run and build commands
```bash
uvicorn backend.app.main:app --reload --port 8000   # dashboard at http://localhost:8000
```
Deploy: push to GitHub, create a Render web service from `render.yaml` (build `./build.sh`).

## Testing instructions
```bash
python -m pytest -q backend/tests
```
Checks: C engine equals the Python fallback; dataset size and 5% fraud rate; a hand-built mule ring is traced correctly; held-out precision/recall floor; API hold → release flow. Manual check: open the dashboard, pick the top alert, press **Check cash-out** (expect HOLD AND VERIFY), press **Release as legitimate** and check again.

## Other configuration
API: `GET /api/summary`, `GET /api/alerts`, `GET /api/alerts/{id}`, `POST /api/alerts/{id}/decision`, `POST /api/check-cashout`, `GET /api/health`. Interactive docs at `/docs`.

## Synthetic data assumptions (documented per upay guideline §11)
1,300 customers, 40 agents, 60 merchants; 30 days; 10,000 transactions of which ~5% are fraud. Normal customers send to ~6 usual contacts, pay merchants, cash in/out with lognormal amounts around their own scale. Fraud rings: victim → hub mule (৳15k–90k) → 3–5 mules → agent cash-out; 30% of rings are "evasive" and wait ~16 min before splitting. No real personal data. Times are Bangladesh time (UTC+6).

## Responsible AI
Privacy: synthetic only. Explainability: reason codes + narrative. Fairness: hold-rate and wrongly-held rate by region and account age on the dashboard. Human oversight: holds are temporary and reviewed. Security: optional API key, input validation.

## Limitations (be upfront with judges)
- The data is simple and self-generated, so scores are very high (held-out AUC ≈ 1.0). At the ≥85 hold line the plain pass-through rule performs about as well as the full model; the model's gain is in the 60–85 review band, which catches the evasive rings the rule misses. Treat numbers as method comparison, not production accuracy.
- Scoring is a batch pass over the dataset. A streaming version would update trails as each transaction arrives.
- Rule and score weights were hand-set on synthetic data and need recalibration with governed real data.
- Fairness grouping uses only synthetic region and account age.
