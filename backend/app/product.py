"""Persistent analyst cases and isolated, past-only synthetic replay."""
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from threading import RLock

import pandas as pd
from .scoring import Sentinel, DATA, HOLD, WATCH

REGIONS = {
    'Dhaka': (23.8103, 90.4125), 'Chattogram': (22.3569, 91.7832),
    'Sylhet': (24.8949, 91.8687), 'Rajshahi': (24.3745, 88.6042),
    'Khulna': (22.8456, 89.5403), 'Barishal': (22.7010, 90.3535),
    'Rangpur': (25.7439, 89.2752), 'Mymensingh': (24.7471, 90.4203),
}
DHAKA_AREAS = [('Mirpur',23.8223,90.3654),('Uttara',23.8759,90.3795),('Motijheel',23.7330,90.4172)]

def now():
    return datetime.now(timezone.utc).isoformat()


def add_locations(a):
    a = a.copy()
    for i, r in a.iterrows():
        lat, lon = REGIONS.get(r.region, REGIONS['Dhaka'])
        area = r.region
        if r.role == 'agent':
            if r.region == 'Dhaka':
                area, lat, lon = DHAKA_AREAS[int(i)%3]
            else:
                area += ' synthetic agent area ' + str(int(i)%4+1)
                lat += ((int(i)%5)-2)*.012
                lon += ((int(i)%7)-3)*.012
        a.loc[i, 'area'], a.loc[i, 'lat'], a.loc[i, 'lon'] = area,lat,lon
    return a


class Store:
    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with self.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, scope TEXT, case_id TEXT, account_id INTEGER, action TEXT, analyst TEXT, note TEXT, outcome TEXT, at TEXT, risk REAL, payload TEXT)')
            c.execute('CREATE TABLE IF NOT EXISTS releases (scope TEXT, case_id TEXT, used INTEGER DEFAULT 0, PRIMARY KEY(scope,case_id))')
    def connect(self):
        return sqlite3.connect(self.path, timeout=20)
    def log(self, scope, case, account, action, analyst='', note='', outcome='', risk=0, payload=None):
        event=dict(scope=scope,case_id=case,account_id=int(account),action=action,analyst=analyst,note=note,outcome=outcome,at=now(),risk=float(risk),payload=payload or {})
        with self.connect() as c:
            c.execute('INSERT INTO audit(scope,case_id,account_id,action,analyst,note,outcome,at,risk,payload) VALUES(?,?,?,?,?,?,?,?,?,?)',tuple(event[k] for k in ('scope','case_id','account_id','action','analyst','note','outcome','at','risk'))+(json.dumps(event['payload']),))
            if action=='release':
                c.execute('INSERT OR REPLACE INTO releases VALUES(?,?,0)',(scope,case))
            elif action in ('confirm_hold','escalate'):
                c.execute('DELETE FROM releases WHERE scope=? AND case_id=?',(scope,case))
        return event
    def history(self, scope, case=None):
        with self.connect() as c:
            c.row_factory=sqlite3.Row
            q='SELECT * FROM audit WHERE scope=?'
            args=[scope]
            if case:
                q+=' AND case_id=?';args.append(case)
            rows=c.execute(q+' ORDER BY id DESC LIMIT 1000',args).fetchall()
        out=[]
        for r in rows:
            v=dict(r);v['payload']=json.loads(v['payload']);out.append(v)
        return out
    def released(self,scope,case,consume=False):
        with self.connect() as c:
            if consume:
                c.execute('BEGIN IMMEDIATE')
                n=c.execute('UPDATE releases SET used=1 WHERE scope=? AND case_id=? AND used=0',(scope,case)).rowcount
                return n==1
            return c.execute('SELECT 1 FROM releases WHERE scope=? AND case_id=? AND used=0',(scope,case)).fetchone() is not None


class Product:
    def __init__(self, s, store, scope='batch'):
        self.s,self.store,self.scope=s,store,scope
        self.s.a=add_locations(self.s.a)
    def case(self,i):
        rows=self.s.t[(self.s.t.src==i)|(self.s.t.dst==i)]
        evidence=rows.to_csv(index=False)+str(tuple(float(self.s.F.loc[i,k]) for k in ('risk','graph','anomaly','rule')))
        fingerprint=hashlib.sha256(evidence.encode()).hexdigest()[:12]
        return f'{i}-{fingerprint}'
    def status(self,i):
        if self.store.released(self.scope,self.case(i)):
            return 'release'
        hs=self.store.history(self.scope,self.case(i))
        decision=next((x for x in hs if x['action'] in ('confirm_hold','escalate')),None)
        return decision['action'] if decision else ('pending_review' if self.s.F.risk[i]>=HOLD else 'none')
    def detail(self,i):
        d=self.s.detail(i);d['case_id']=self.case(i);d['status']=self.status(i)
        d['history']=self.store.history(self.scope,d['case_id'])
        d['location']=self.location(i)
        for n in d['trail']['nodes']:
            n.update(self.location(n['id']))
        d['timeline']=sorted(d['trail']['edges'],key=lambda e:e['ts'])
        d['contributions']={k:round(float(self.s.F.loc[i,k])*w,3) for k,w in [('graph',40),('anomaly',40),('rule',20)]}
        # Historic cash-out destinations before the case's inflow; distance alone is not scored.
        b=self.s.best_in.get(i)
        geo=[]
        if b is not None:
            for e in d['trail']['edges']:
                if e['type']!='cash_out': continue
                region=self.s.a.loc[e['dst'],'region']
                past=self.s.t[(self.s.t.src==e['src'])&(self.s.t.type=='cash_out')&(self.s.t.ts<int(b.ts))]
                known=set(self.s.a.loc[past.dst.values,'region'])
                if known and region not in known and e['ts']-int(b.ts)<=1800:
                    geo.append(dict(code='UNUSUAL_CASHOUT_ROUTE',text=f"{self.s.a.loc[e['src'],'label']} reached a previously unseen cash-out region ({region}) within {int(e['ts']-b.ts)} seconds of the traced inflow. Supporting route evidence only; no GPS or distance-based hold."))
        if geo:d['reasons'].extend(geo[:3]);d['narrative']['why'].extend(x['text'] for x in geo[:3])
        d['location_note']='Synthetic area locations; no real user tracking. Customer markers are division/city centroids.'
        return d
    def location(self,i):
        r=self.s.a.loc[i]
        return dict(region=r.region,area=r.area,lat=float(r.lat),lon=float(r.lon))
    def alerts(self,min_risk=60,limit=100):
        rows=self.s.alerts(min_risk,limit)
        for r in rows:r.update(status=self.status(r['id']),region=self.s.a.loc[r['id'],'region'])
        return rows
    def decide(self,i,action,analyst,note,outcome=''):
        if self.s.F.role[i]!='customer':raise ValueError('Only customer cases accept decisions.')
        return self.store.log(self.scope,self.case(i),i,action,analyst,note,outcome,self.s.F.risk[i],{'evidence':self.detail(i)['reasons'],'release_policy':'one subsequent cash-out check for unchanged evidence'})
    def cashout(self,i,amount):
        if self.s.F.role[i]!='customer':raise ValueError('Only customer wallets are scored.')
        approved=self.store.released(self.scope,self.case(i),consume=True)
        f=self.s.F.loc[i]
        # A monitored downstream wallet linked to a currently held upstream hub
        # is queued for human review before cash-out. This is a distinct policy rule.
        recent=self.s.t[(self.s.t.dst==i)&(self.s.t.amount>=2000)&(self.s.t.ts>=int(self.s.t.ts.max())-1800)]
        upstream=[int(x) for x in recent.src if self.s.F.risk[int(x)]>=HOLD]
        network_hold=bool(upstream) and f.risk>=WATCH
        decision='HOLD_AND_VERIFY' if (f.risk>=HOLD or network_hold) and not approved else 'ALLOW_WITH_MONITORING' if f.risk>=WATCH else 'ALLOW' 
        result=dict(decision=decision,risk=float(f.risk),amount=amount,release_consumed=approved,policy_reason='RECENT_HIGH_RISK_UPSTREAM' if network_hold and f.risk<HOLD else 'ACCOUNT_RISK_THRESHOLD',upstream_accounts=upstream,message='Demo cash-out paused for analyst review; verification request simulated.' if decision=='HOLD_AND_VERIFY' else 'Demo request allowed. No money is moved.',reasons=self.detail(i)['reasons'][:3])
        self.store.log(self.scope,self.case(i),i,'cashout_check',risk=f.risk,payload=result)
        return result
    def summary(self):
        d=self.s.summary();ids=self.s.F.index[(self.s.F.role=='customer')&(self.s.F.risk>=HOLD)]
        statuses=[self.status(int(i)) for i in ids]
        d['on_hold']=sum(x!='release' for x in statuses);d['pending']=statuses.count('pending_review')
        d['released']=statuses.count('release');d['scope']=self.scope
        d['metrics_note']='Retrospective synthetic batch comparison, not a pre-cash-out prevention rate or production accuracy.'
        return d
    def hotspots(self):
        rows=self.s.t[self.s.t.type=='cash_out']
        out=[]
        for region in REGIONS:
            group=rows[rows.dst.isin(self.s.a.index[self.s.a.region==region])]
            flagged=group[group.src.isin(self.s.F.index[self.s.F.risk>=WATCH])]
            out.append(dict(region=region,lat=REGIONS[region][0],lon=REGIONS[region][1],total=len(group),flagged=len(flagged),flagged_amount=float(flagged.amount.sum())))
        return out
    def agent(self,i):
        group=self.s.t[(self.s.t.dst==i)&(self.s.t.type=='cash_out')]
        flagged=group[group.src.isin(self.s.F.index[self.s.F.risk>=WATCH])]
        return dict(id=i,label=self.s.a.label[i],location=self.location(i),cashouts=len(group),flagged_cashouts=len(flagged),flagged_amount=float(flagged.amount.sum()),wallets=sorted(set(int(x) for x in flagged.src)),note='Connected flagged wallets are investigative evidence, not proof of agent wrongdoing.')


class Replay:
    def __init__(self, batch,store):
        self.lock=RLock();self.batch=batch;self.store=store;self.run=0
        # Train on the first 70% in time, never on the replay suffix or scripted future events.
        self.cut=int(len(batch.t)*.7)
        self.base=batch.t.iloc[:self.cut].copy()
        self.model=Sentinel.__new__(Sentinel)
        self.model.a=batch.a.copy();self.model.t=self.base.copy();self.model.decisions={}
        self.model._fit(42)
        self.reset('scam')
    def reset(self, scenario='scam'):
        with self.lock:
            self.run+=1;self.scenario=scenario;self.cursor=0;self.events=[];self.checks=[];self.first_watch=None
            self.model.t=self.base.copy();self.model.a=self.batch.a.copy()
            self.model._fit(42,frozen=True)
            self.product=Product(self.model,self.store,f'replay-{now()}-{self.run}')
            safe=self.model.F[(self.model.F.role=='customer')&(self.model.F.risk<30)].index.tolist()
            self.victim,self.hub,self.m1,self.m2,self.m3=safe[:5]
            agents=self.model.a.index[self.model.a.role=='agent'].tolist()
            self.focus=self.hub
            start=int(self.base.ts.max())+60
            specs=[(0,self.victim,self.hub,57000,'send_money')]
            delay=1000 if scenario=='evasive' else 120
            if scenario=='legitimate':
                specs += [(3600,self.hub,agents[0],5000,'cash_out')]
            else:
                for j,m in enumerate([self.m1,self.m2,self.m3]):
                    specs.append((delay+j*30,self.hub,m,18500,'send_money'))
                for j,m in enumerate([self.m1,self.m2,self.m3]):
                    specs.append((delay+240+j*30,m,agents[j],17900,'cash_out'))
            if scenario=='dataset':
                self.queue=self.batch.t.iloc[self.cut:].to_dict('records')
            else:
                self.queue=[dict(txn_id=f'SIM-{self.run}-{j}',ts=start+sec,src=int(src),dst=int(dst),amount=float(amt),type=typ,channel='agent_point' if typ=='cash_out' else 'app',device_id=f'D{src}',is_fraud=int(scenario!='legitimate'),pattern='scripted_'+scenario) for j,(sec,src,dst,amt,typ) in enumerate(sorted(specs))]
            return self.view()
    def step(self):
        with self.lock:
            if self.cursor>=len(self.queue):return self.view()
            row=self.queue[self.cursor].copy();self.cursor+=1
            pre=float(self.model.F.risk[row['src']])
            # Evaluate BEFORE adding the cash-out event, so future completion is unavailable.
            if row['type']=='cash_out':
                result=self.product.cashout(int(row['src']),float(row['amount']))
                self.checks.append(dict(txn_id=row['txn_id'],amount=float(row['amount']),is_fraud=int(row['is_fraud']),decision=result['decision'],pre_risk=pre))
                row['pre_decision']=result['decision']
            self.model.t=pd.concat([self.model.t,pd.DataFrame([{k:v for k,v in row.items() if k!='pre_decision'}])],ignore_index=True)
            self.model._fit(42,frozen=True)
            score=float(self.model.F.risk[self.focus])
            if score>=WATCH and self.first_watch is None:self.first_watch=int(row['ts'])
            row['focus_risk']=score;row['pre_risk']=pre;self.events.append(row)
            return self.view()
    def view(self):
        fraud=sum(x['amount'] for x in self.checks if x['is_fraud'])
        held=sum(x['amount'] for x in self.checks if x['is_fraud'] and x['decision']=='HOLD_AND_VERIFY')
        fp=sum(x['decision']=='HOLD_AND_VERIFY' and not x['is_fraud'] for x in self.checks)
        return dict(scenario=self.scenario,cursor=self.cursor,total=len(self.queue),done=self.cursor>=len(self.queue),focus=int(self.focus),victim=int(self.victim),scope=self.product.scope,events=self.events[-100:],checks=self.checks[-100:],impact=dict(observed_fraud_cashout_requests=fraud,paused_before_cashout=held,prevention_rate=round(held/fraud,3) if fraud else None,legitimate_requests_paused=int(fp),detection_delay_seconds=(self.first_watch-int(self.queue[0]['ts'])) if self.first_watch is not None else None),model_note='Frozen Isolation Forest trained on first 70% of transactions in chronological order. Features update from observed events only. Replay records are observed ledger events even when the parallel demo gate says pause; this is a counterfactual gate comparison, not actual funds recovered.')
