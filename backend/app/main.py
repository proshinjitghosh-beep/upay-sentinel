import os
from contextlib import asynccontextmanager
from typing import Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .scoring import DATA, ROOT, Sentinel

state = {}


@asynccontextmanager
async def lifespan(app):
    if not os.path.exists(os.path.join(DATA, "transactions.csv")):
        from data.generate_data import main as generate
        generate()
    state["s"] = Sentinel()
    yield


app = FastAPI(title="Upay Sentinel", version="1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("ALLOWED_ORIGINS", "*").split(","),
                   allow_methods=["*"], allow_headers=["*"])


def guard(x_api_key: Optional[str] = Header(default=None)):
    key = os.getenv("SENTINEL_API_KEY")
    if key and x_api_key != key:
        raise HTTPException(401, "Missing or invalid X-API-Key header.")


def sent(i: Optional[int] = None) -> Sentinel:
    s = state["s"]
    if i is not None and i not in s.F.index:
        raise HTTPException(404, "Unknown account id.")
    return s


class Decision(BaseModel):
    action: Literal["confirm_hold", "release", "escalate"]
    analyst: str = "analyst"
    note: str = ""


class CashOut(BaseModel):
    account_id: int
    amount: float


@app.get("/api/health")
def health():
    return {"status": "ok", "engine": state["s"].summary()["engine"]}


@app.get("/api/summary")
def summary():
    return sent().summary()


@app.get("/api/alerts")
def alerts(min_risk: float = 60, limit: int = 100):
    return sent().alerts(min_risk, limit)


@app.get("/api/alerts/{acct_id}")
def alert_detail(acct_id: int):
    return sent(acct_id).detail(acct_id)


@app.post("/api/alerts/{acct_id}/decision", dependencies=[Depends(guard)])
def decide(acct_id: int, d: Decision):
    return sent(acct_id).decide(acct_id, d.action, d.analyst, d.note)


@app.post("/api/check-cashout", dependencies=[Depends(guard)])
def check_cashout(c: CashOut):
    try:
        return sent(c.account_id).check_cashout(c.account_id, c.amount)
    except KeyError as e:
        raise HTTPException(400, str(e))


@app.get("/")
def index():
    return FileResponse(os.path.join(ROOT, "frontend", "index.html"))
