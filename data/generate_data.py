"""Synthetic MFS transaction generator for Upay Sentinel.

Produces data/transactions.csv (10,000 rows, ~5% fraud) and data/accounts.csv.
No real personal data is used. Fraud is injected as scam->mule->cash-out rings:
  victim --(large send)--> mule A --(split within minutes)--> mules m1..mk --(cash-out)--> agents
"""
import csv
import os
import random
from datetime import datetime, timezone

import numpy as np

try:
    from faker import Faker
except ImportError:  # names fall back to "User N"
    Faker = None

OUT = os.path.dirname(os.path.abspath(__file__))
N_CUST, N_AGENT, N_MERCH = 1300, 40, 60
N_TXN, FRAUD_TARGET, N_POOL, DAYS = 10_000, 500, 120, 30
REGIONS = ["Dhaka", "Chattogram", "Sylhet", "Rajshahi", "Khulna", "Barishal", "Rangpur", "Mymensingh"]
REGION_W = [.38, .16, .08, .10, .09, .05, .07, .07]
T0 = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp())


def main(out_dir=OUT, seed=42):
    rng, nprng = random.Random(seed), np.random.default_rng(seed)
    fk = None
    if Faker:
        fk = Faker()
        Faker.seed(seed)

    agents = list(range(N_CUST, N_CUST + N_AGENT))
    merchants = list(range(N_CUST + N_AGENT, N_CUST + N_AGENT + N_MERCH))
    pool = set(rng.sample(range(N_CUST), N_POOL))  # accounts that may be recruited as mules

    accts = []
    for i in range(N_CUST):
        accts.append(dict(account_id=i, role="customer", label=f"W{i:05d}",
                          name=fk.name() if fk else f"User {i}",
                          region=rng.choices(REGIONS, REGION_W)[0],
                          age_band="new" if (i in pool or rng.random() < .12) else "established",
                          scale=float(nprng.lognormal(np.log(900), .5)), hour_c=rng.choice([10, 13, 17, 20]),
                          is_mule=0))
    for k, i in enumerate(agents):
        accts.append(dict(account_id=i, role="agent", label=f"AGT-{k:03d}", name=f"Agent {k}",
                          region=rng.choices(REGIONS, REGION_W)[0], age_band="established", scale=0, hour_c=12, is_mule=0))
    for k, i in enumerate(merchants):
        accts.append(dict(account_id=i, role="merchant", label=f"MER-{k:03d}", name=fk.company() if fk else f"Merchant {k}",
                          region=rng.choices(REGIONS, REGION_W)[0], age_band="established", scale=0, hour_c=12, is_mule=0))
    contacts = {i: rng.sample(range(N_CUST), 6) for i in range(N_CUST)}

    # ---- fraud rings -------------------------------------------------------
    fraud, used = [], set()
    pool_l = sorted(pool)
    victims = [i for i in range(N_CUST) if i not in pool]
    while len(fraud) < FRAUD_TARGET:
        victim, A = rng.choice(victims), rng.choice(pool_l)
        ms = rng.sample([p for p in pool_l if p != A], rng.randint(3, 5))
        t, V = T0 + rng.randrange(DAYS * 86400 - 3600), rng.randrange(15_000, 90_001, 500)
        slow = rng.random() < .3  # evasive ring: waits ~16-18 min before splitting to dodge simple speed rules
        fraud.append((t, victim, A, V, "send_money", "app", f"D{victim}", 1, "scam_inflow"))
        w = nprng.dirichlet(np.ones(len(ms)) * 4)
        for m, share in zip(ms, w):
            sh = int(V * share)
            t2 = t + (rng.randint(950, 1100) if slow else rng.randint(30, 400))
            fraud.append((t2, A, m, sh, "send_money", "app", f"DM{A}", 1, "mule_split"))
            fraud.append((t2 + (rng.randint(300, 600) if slow else rng.randint(60, 900)), m, rng.choice(agents), int(sh * .97),
                          "cash_out", "agent_point", f"DM{m}", 1, "mule_cash_out"))
            used |= {A, m}
    for a in accts:
        a["is_mule"] = int(a["account_id"] in used)

    # ---- normal behaviour --------------------------------------------------
    rows = []
    for _ in range(N_TXN - len(fraud)):
        c = rng.randrange(N_CUST)
        a, r = accts[c], rng.random()
        if r < .35:
            typ, src = "send_money", c
            dst = rng.choice(contacts[c]) if rng.random() < .85 else rng.randrange(N_CUST)
        elif r < .60:
            typ, src, dst = "cash_out", c, rng.choice(agents)
        elif r < .90:
            typ, src, dst = "payment", c, rng.choice(merchants)
        else:
            typ, src, dst = "cash_in", rng.choice(agents), c
        if dst == src:
            dst = (dst + 1) % N_CUST
        amt = int(round(min(max(nprng.lognormal(np.log(a["scale"]), .6), 50), 25_000), -1))
        hour = int(nprng.normal(a["hour_c"], 3)) % 24  # Bangladesh local hour
        ts = T0 + rng.randrange(DAYS) * 86400 + ((hour - 6) % 24) * 3600 + rng.randrange(3600)
        dev = f"D{c}" if rng.random() < .95 else f"DN{c}"
        rows.append((ts, src, dst, amt, typ, "app" if rng.random() < .8 else "ussd", dev, 0, "normal"))

    allr = sorted(rows + fraud, key=lambda x: x[0])
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "transactions.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["txn_id", "ts", "src", "dst", "amount", "type", "channel", "device_id", "is_fraud", "pattern"])
        for n, r in enumerate(allr):
            w.writerow([f"T{n:06d}", *r])
    with open(os.path.join(out_dir, "accounts.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["account_id", "role", "label", "name", "region", "age_band", "is_mule"])
        for a in accts:
            w.writerow([a[k] for k in ("account_id", "role", "label", "name", "region", "age_band", "is_mule")])
    print(f"{len(allr)} transactions, {len(fraud)} fraud ({len(fraud)/len(allr):.1%}), {len(used)} mule accounts")


if __name__ == "__main__":
    main()
