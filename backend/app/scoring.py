"""Risk scoring: C graph engine + Isolation Forest + NetworkX propagation + separate business rules."""
import os
from datetime import datetime, timedelta, timezone

import networkx as nx
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import roc_auc_score

from .engine import ENGINE, trace_flows

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, "data")
WINDOW, HOLD, WATCH = 1800, 85, 60  # trail window (s); risk thresholds
FEATS = ["log_max_in", "log_max_out", "pass_through", "uniq_out", "n_in", "n_out",
         "night_ratio", "log_dwell", "reach", "depth", "cashout_ratio"]


def dur(s):
    s = int(s)
    return f"{s} s" if s < 120 else f"{s // 60} min"


def when(ts):
    return (datetime.fromtimestamp(int(ts), timezone.utc) + timedelta(hours=6)).strftime("%d %b, %H:%M")


def _pr(y, p):
    tp, fp, fn = int((y & p).sum()), int((~y & p).sum()), int((y & ~p).sum())
    pr, rc = tp / max(tp + fp, 1), tp / max(tp + fn, 1)
    return dict(tp=tp, fp=fp, fn=fn, precision=round(pr, 3), recall=round(rc, 3),
                f1=round(2 * pr * rc / max(pr + rc, 1e-9), 3))


class Sentinel:
    def __init__(self, data_dir=DATA, seed=42):
        self.t = pd.read_csv(os.path.join(data_dir, "transactions.csv"))
        self.a = pd.read_csv(os.path.join(data_dir, "accounts.csv")).set_index("account_id", drop=False).sort_index()
        self.decisions = {}
        self._fit(seed)

    # ------------------------------------------------------------------ model
    def _fit(self, seed, frozen=False):
        t, a = self.t, self.a
        N = len(a)
        roles = a.role.values
        g = trace_flows(N, t.src.values, t.dst.values, t.amount.values, t.ts.values, roles == "agent", WINDOW)
        inc = t.groupby("dst").amount.agg(in_sum="sum", n_in="count", max_in="max")
        hr = ((t.ts + 6 * 3600) % 86400) // 3600
        t = t.assign(night=((hr < 5) | (hr >= 23)).astype(int))
        out = t.groupby("src").agg(out_sum=("amount", "sum"), n_out=("amount", "count"), max_out=("amount", "max"),
                                   uniq_out=("dst", "nunique"), night_ratio=("night", "mean"))
        F = pd.concat([inc, out], axis=1).reindex(range(N)).fillna(0)
        for k, v in g.items():
            F[k] = v
        F["cashout_ratio"] = np.minimum(F.cash_amt / (F.max_in + 1), 1)
        F["pass_through"] = np.minimum(F.out_sum / (F.in_sum + 1), 2)
        F["log_max_in"], F["log_max_out"] = np.log1p(F.max_in), np.log1p(F.max_out)
        F["log_dwell"] = np.log1p(np.where(F.dwell >= 0, F.dwell, 7200))

        # graph score (C-engine features) + one-hop propagation from high-risk upstream accounts
        speed = np.where(F.dwell >= 0, 1 - np.minimum(F.dwell / 900, 1), 0)
        own = F.cashout_ratio * (.5 * np.minimum(F.reach / 6, 1) + .3 * np.minimum(F.depth / 2, 1) + .2 * speed)
        # A rapid meaningful split can be visible before any cash-out completes.
        early = ((F.max_in >= 15000) & (F.reach >= 3) & (F.dwell >= 0)
                 & (F.dwell <= 900) & (F.pass_through >= .7)).astype(float) * .85
        own = np.maximum(own, early)
        F["early_split"] = early
        G = nx.DiGraph()
        for r in t.itertuples():
            if G.has_edge(r.src, r.dst):
                G[r.src][r.dst]["amt"] += r.amount
            else:
                G.add_edge(r.src, r.dst, amt=r.amount)
        prop = np.zeros(N)
        for u in np.where(own.values >= .7)[0]:
            for w, d in G[u].items():
                if roles[w] == "customer" and d["amt"] >= 2000:
                    prop[w] = max(prop[w], .9 * own.values[u])
        F["graph_own"], F["graph_prop"] = own, prop
        F["graph"] = np.maximum(own, prop)

        # business rule (kept separate from ML): rapid pass-through of a meaningful inflow
        F["rule"] = ((F.max_in >= 2000) & (F.dwell >= 0) & (F.dwell <= 900) & (F.pass_through >= .7)).astype(int)

        # behavioural anomaly: Isolation Forest fit on a 70% training split of customers
        cust = a.index[roles == "customer"].values
        perm = np.random.default_rng(seed).permutation(cust)
        train, F["split"] = perm[: int(len(perm) * .7)], "n/a"
        F.loc[perm[: int(len(perm) * .7)], "split"] = "train"
        F.loc[perm[int(len(perm) * .7):], "split"] = "test"
        if not frozen:
            self.iso = IsolationForest(n_estimators=200, random_state=seed).fit(F.loc[train, FEATS])
            self.ref = np.sort(-self.iso.score_samples(F.loc[train, FEATS]))
        raw = -self.iso.score_samples(F[FEATS])
        ref = self.ref
        F["anomaly"] = np.searchsorted(ref, raw) / len(ref)

        F["risk"] = np.where(roles == "customer",
                             100 * (.4 * F.graph + .4 * F.anomaly + .2 * F.rule), 0).round(1)
        F["is_mule"] = a.is_mule.values
        F["role"], F["region"], F["age_band"], F["label"] = roles, a.region.values, a.age_band.values, a.label.values
        self.F, self.G = F, G

        self.best_in = {}
        idx = self.t.groupby("dst").amount.idxmax()
        for r in self.t.loc[idx.values].itertuples():
            self.best_in[int(r.dst)] = r
        self.adj = {int(k): sorted(zip(v.ts, v.dst, v.amount, v.type)) for k, v in self.t.groupby("src")}
        self._metrics()

    def _metrics(self):
        F = self.F
        c = F[F.role == "customer"]
        te = c[c.split == "test"]
        y = te.is_mule.values.astype(bool)
        fr = self.t[(self.t.is_fraud == 1) & (self.t.type == "cash_out")]
        held = F.loc[fr.src.values, "risk"].values >= HOLD
        self.metrics = dict(
            holdout_accounts=len(te), holdout_mules=int(y.sum()), hold_threshold=HOLD,
            sentinel=_pr(y, te.risk.values >= HOLD), sentinel_watch_or_above=_pr(y, te.risk.values >= WATCH), rule_only=_pr(y, te.rule.values == 1),
            anomaly_only=_pr(y, te.anomaly.values >= .95),
            auc=round(float(roc_auc_score(y, te.risk)), 3) if 0 < y.sum() < len(y) else None,
            fraud_cashout_value_held=round(float(fr.amount.values[held].sum() / max(fr.amount.sum(), 1)), 3),
        )
        nm = c[c.is_mule == 0]
        self.fairness = {}
        for col in ("region", "age_band"):
            rows = []
            for k, grp in c.groupby(col):
                ng = grp[grp.is_mule == 0]
                rows.append(dict(group=k, accounts=len(grp), flag_rate=round(float((grp.risk >= HOLD).mean()), 3),
                                 false_positive_rate=round(float((ng.risk >= HOLD).mean()) if len(ng) else 0, 3)))
            self.fairness[col] = rows

    # ------------------------------------------------------------ explanations
    def tier(self, risk):
        return "HOLD" if risk >= HOLD else "WATCH" if risk >= WATCH else "CLEAR"

    def reasons(self, i):
        f, out = self.F.loc[i], []
        if f.early_split:
            out.append(("PRE_CASHOUT_FAN_OUT", "A meaningful inflow rapidly reached at least three accounts; graph structure can raise risk before a cash-out completes."))
        if f.rule:
            out.append(("RAPID_PASS_THROUGH", f"Received {_tk(f.max_in)} and forwarded {f.pass_through:.0%} of inflow within {dur(f.dwell)}."))
        if f.reach >= 3:
            out.append(("FAN_OUT", f"Funds fanned out to {int(f.reach)} accounts across {int(f.depth)} hops inside {dur(WINDOW)}."))
        if f.cashout_ratio >= .5:
            out.append(("CASH_OUT_REACH", f"{_tk(f.cash_amt)} ({f.cashout_ratio:.0%} of the inflow) reached cash-out agents within {dur(f.span)}."))
        if f.graph_prop > f.graph_own:
            out.append(("UPSTREAM_FLAGGED", "Receives funds from an account the graph engine already scores as a mule hub."))
        if f.anomaly >= .9:
            out.append(("BEHAVIOUR_ANOMALY", f"Behaviour is more unusual than {f.anomaly:.0%} of comparable accounts (Isolation Forest)."))
        if f.age_band == "new":
            out.append(("NEW_ACCOUNT", "Account is under 90 days old."))
        return [dict(code=c, text=x) for c, x in out]

    def trail(self, i):
        b = self.best_in.get(i)
        if b is None:
            return dict(nodes=[], edges=[])
        t0, depth, arrive, q = int(b.ts), {i: 0, int(b.src): -1}, {i: int(b.ts)}, [i]
        edges = [dict(src=int(b.src), dst=i, amount=float(b.amount), ts=t0, type=b.type)]
        while q:
            u = q.pop(0)
            for ts, w, amt, typ in self.adj.get(u, []):
                if ts < arrive[u] or ts > t0 + WINDOW:
                    continue
                w = int(w)
                edges.append(dict(src=u, dst=w, amount=float(amt), ts=int(ts), type=typ))
                if w not in depth:
                    depth[w], arrive[w] = depth[u] + 1, int(ts)
                    if self.F.role[w] != "agent":
                        q.append(w)
        nodes = [dict(id=n, label=self.F.label[n], role=self.F.role[n], depth=d, risk=float(self.F.risk[n]))
                 for n, d in depth.items()]
        return dict(nodes=nodes, edges=edges)

    def narrative(self, i):
        f, tr = self.F.loc[i], self.trail(i)
        ag = sum(n["role"] == "agent" for n in tr["nodes"])
        cu = sum(n["role"] == "customer" and n["depth"] > 0 for n in tr["nodes"])
        b, tier = self.best_in.get(i), self.tier(f.risk)
        what = "No inbound transfer to trace."
        if b is not None:
            what = (f"{f.label} received {_tk(b.amount)} on {when(b.ts)} (Bangladesh time). Inside {dur(WINDOW)} the money "
                    f"moved on to {cu} further customer accounts and {_tk(f.cash_amt)} landed on {ag} cash-out agents.")
        nxt = {"HOLD": ["Place a temporary hold on pending cash-outs from this account (not a permanent block).",
                        "Ask for live face verification before releasing anything.",
                        "Analyst reviews the trail below and confirms, releases or escalates; contact the sender in case they are a scam victim."],
               "WATCH": ["Keep cash-outs allowed but monitor this account.", "Re-score if new inflows arrive."],
               "CLEAR": ["No action needed."]}[tier]
        return dict(what=what, why=[r["text"] for r in self.reasons(i)] or ["No strong risk signals."], next=nxt,
                    note="Written from structured evidence by a fixed template; the score and decision come from the model and rules, not from free-form text.")

    # --------------------------------------------------------------- API views
    def status(self, i):
        d = self.decisions.get(i)
        return d["action"] if d else ("pending_review" if self.F.risk[i] >= HOLD else "none")

    def alerts(self, min_risk=WATCH, limit=100):
        c = self.F[(self.F.role == "customer") & (self.F.risk >= min_risk)].sort_values("risk", ascending=False).head(limit)
        return [dict(id=int(i), label=r.label, risk=float(r.risk), tier=self.tier(r.risk), status=self.status(i),
                     max_in=float(r.max_in), top_reason=(self.reasons(i) or [dict(code="-")])[0]["code"])
                for i, r in c.iterrows()]

    def detail(self, i):
        f = self.F.loc[i]
        return dict(id=i, label=f.label, risk=float(f.risk), tier=self.tier(f.risk), status=self.status(i),
                    decision=self.decisions.get(i), reasons=self.reasons(i), trail=self.trail(i), narrative=self.narrative(i),
                    components=dict(graph=round(float(f.graph), 2), anomaly=round(float(f.anomaly), 2), rule=int(f.rule)))

    def decide(self, i, action, analyst, note):
        self.decisions[i] = dict(action=action, analyst=analyst, note=note, at=datetime.now(timezone.utc).isoformat())
        return self.decisions[i]

    def check_cashout(self, i, amount):
        f = self.F.loc[i]
        if f.role != "customer":
            raise KeyError("Only customer wallets are scored.")
        released = self.decisions.get(i, {}).get("action") == "release"
        if f.risk >= HOLD and not released:
            return dict(decision="HOLD_AND_VERIFY", risk=float(f.risk), amount=amount,
                        message="Cash-out paused temporarily. Live face verification requested and case sent to an analyst.",
                        reasons=self.reasons(i)[:3])
        if f.risk >= WATCH:
            return dict(decision="ALLOW_WITH_MONITORING", risk=float(f.risk), amount=amount, message="Allowed; account is being monitored.", reasons=self.reasons(i)[:2])
        return dict(decision="ALLOW", risk=float(f.risk), amount=amount, message="No risk signals.", reasons=[])

    def summary(self):
        c = self.F[self.F.role == "customer"]
        return dict(engine=ENGINE, transactions=len(self.t), accounts=len(self.a), fraud_rate=round(float(self.t.is_fraud.mean()), 3),
                    on_hold=int((c.risk >= HOLD).sum()), watch=int(((c.risk >= WATCH) & (c.risk < HOLD)).sum()),
                    pending=sum(self.status(i) == "pending_review" for i in c.index[c.risk >= HOLD]),
                    metrics=self.metrics, fairness=self.fairness)


def _tk(n):
    return "৳" + f"{round(float(n)):,}"
