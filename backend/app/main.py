import os
import json
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from threading import RLock
from typing import Literal, Optional
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from .scoring import DATA, ROOT, Sentinel
from .product import Product, Store, Replay
from .demo_auth import DemoAuth
state={};lock=RLock()
@asynccontextmanager
async def lifespan(app):
    if not os.path.exists(os.path.join(DATA,'transactions.csv')):
        from data.generate_data import main
        main()
    s=Sentinel();store=Store(os.getenv('SENTINEL_DB',os.path.join(DATA,'sentinel.sqlite3')))
    state.update(s=s,store=store,p=Product(s,store),replay=Replay(s,store),auth=DemoAuth(os.getenv('SENTINEL_AUTH_DB',os.path.join(DATA,'demo_auth.sqlite3'))))
    yield
app=FastAPI(title='Upay Sentinel',version='2.0',lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=os.getenv('ALLOWED_ORIGINS','http://localhost:8000,http://127.0.0.1:8000').split(','),allow_methods=['GET','POST'],allow_headers=['Content-Type','X-API-Key'])
def guard(x_api_key:Optional[str]=Header(default=None)):
    key=os.getenv('SENTINEL_API_KEY')
    if key and key!=x_api_key:raise HTTPException(401,'Missing or invalid API key.')
def product(scope='batch',i=None):
    p=state['p'] if scope=='batch' else state['replay'].product
    if i is not None and i not in p.s.F.index:raise HTTPException(404,'Unknown account id.')
    return p
class Decision(BaseModel):
    action:Literal['confirm_hold','release','escalate']
    analyst:str=Field(default='demo-analyst',min_length=1,max_length=80)
    note:str=Field(default='',max_length=2000)
    outcome:Literal['','suspicious','legitimate','insufficient_evidence']=''
class CashOut(BaseModel):
    account_id:int=Field(ge=0)
    amount:float=Field(gt=0,le=1000000,allow_inf_nan=False)
class Scenario(BaseModel):
    scenario:Literal['scam','evasive','legitimate','dataset']='scam'
class SenderCheck(BaseModel):
    session_token:str=Field(default="",max_length=200)
    sender_id:int=Field(ge=0)
    recipient_id:int=Field(ge=0)
    amount:float=Field(gt=0,le=1000000,allow_inf_nan=False)
@app.get('/api/health')
def health():return {'status':'ok','engine':state['s'].summary()['engine'],'version':'2.0'}
@app.get('/api/summary')
def summary(scope:Literal['batch','replay']='batch'):
    with lock:return product(scope).summary()
@app.get('/api/alerts')
def alerts(scope:Literal['batch','replay']='batch',min_risk:float=Query(60,ge=0,le=100),limit:int=Query(100,ge=1,le=1400)):
    with lock:return product(scope).alerts(min_risk,limit)
@app.get('/api/alerts/{acct_id}')
def detail(acct_id:int,scope:Literal['batch','replay']='batch'):
    with lock:return product(scope,acct_id).detail(acct_id)
@app.post('/api/alerts/{acct_id}/decision',dependencies=[Depends(guard)])
def decide(acct_id:int,d:Decision,scope:Literal['batch','replay']='batch'):
    with lock:
        try:return product(scope,acct_id).decide(acct_id,d.action,d.analyst,d.note,d.outcome)
        except ValueError as e:raise HTTPException(400,str(e))
@app.post('/api/check-cashout',dependencies=[Depends(guard)])
def cashout(c:CashOut,scope:Literal['batch','replay']='batch'):
    with lock:
        try:return product(scope,c.account_id).cashout(c.account_id,c.amount)
        except ValueError as e:raise HTTPException(400,str(e))
@app.get('/api/audit')
def audit(scope:Literal['batch','replay']='batch'):
    with lock:return state['store'].history(product(scope).scope)
@app.get('/api/hotspots')
def hotspots(scope:Literal['batch','replay']='batch'):
    with lock:return product(scope).hotspots()
@app.get('/api/agents/{acct_id}')
def agent(acct_id:int,scope:Literal['batch','replay']='batch'):
    with lock:
        p=product(scope,acct_id)
        if p.s.F.role[acct_id]!='agent':raise HTTPException(400,'Select an agent.')
        return p.agent(acct_id)
@app.get('/api/replay')
def replay():
    with lock:return state['replay'].view()
@app.post('/api/replay/reset',dependencies=[Depends(guard)])
def reset(s:Scenario):
    with lock:
        result=state['replay'].reset(s.scenario)
        state['store'].log(result['scope'],'run',result['focus'],'demo_reset',payload={'scenario':s.scenario})
        return result
@app.post('/api/replay/step',dependencies=[Depends(guard)])
def step():
    with lock:return state['replay'].step()
class DemoLogin(BaseModel):
    wallet_id:int=Field(ge=0)
    pin:str=Field(min_length=5,max_length=5,pattern=r'^\d{5}$')
class DemoLogout(BaseModel):
    session_token:str=Field(max_length=200)
def customer_wallet(wallet_id,scope):
    p=product(scope,wallet_id)
    if p.s.F.role[wallet_id]!='customer':raise HTTPException(400,'Use a synthetic customer wallet.')
def require_demo_session(c):
    if not state['auth'].verify(c.sender_id,c.session_token):
        raise HTTPException(401,'Demo wallet locked or session expired. Sign in with the demo PIN.')
@app.get('/api/demo-auth/status')
def demo_auth_status(wallet_id:int=Query(ge=0),scope:Literal['batch','replay']='batch'):
    with lock:
        customer_wallet(wallet_id,scope)
        return state['auth'].status(wallet_id)
@app.post('/api/demo-auth/login',dependencies=[Depends(guard)])
def demo_login(c:DemoLogin,scope:Literal['batch','replay']='batch'):
    with lock:
        customer_wallet(c.wallet_id,scope)
        result=state['auth'].login(c.wallet_id,c.pin)
        if not result['ok']:
            state['store'].log(product(scope).scope,'demo-auth',c.wallet_id,'demo_pin_locked' if result['locked'] else 'demo_pin_failed',payload={'attempts_remaining':result['attempts_remaining']})
        return result
@app.post('/api/demo-auth/logout',dependencies=[Depends(guard)])
def demo_logout(c:DemoLogout):
    state['auth'].logout(c.session_token)
    return {'ok':True}
@app.post('/api/sender-check',dependencies=[Depends(guard)])
def sender_check(c:SenderCheck,scope:Literal['batch','replay']='batch'):
    with lock:
        require_demo_session(c)
        p=product(scope,c.sender_id);product(scope,c.recipient_id)
        if c.sender_id==c.recipient_id:raise HTTPException(400,'Sender and recipient must differ.')
        if p.s.F.role[c.sender_id]!='customer' or p.s.F.role[c.recipient_id]!='customer':raise HTTPException(400,'Use customer wallets.')
        known=bool(((p.s.t.src==c.sender_id)&(p.s.t.dst==c.recipient_id)&(p.s.t.type=='send_money')).any())
        risk=float(p.s.F.risk[c.recipient_id]);warn=risk>=60 or (not known and c.amount>=15000)
        return dict(warning=warn,recipient_risk=risk,new_recipient=not known,message='Pause and verify who you are paying. Never send money for a prize or share your PIN/OTP.' if warn else 'Check the recipient and amount before sending. A low score does not guarantee safety.',message_bn='পাঠানোর আগে প্রাপকের পরিচয় যাচাই করুন। পুরস্কারের জন্য টাকা পাঠাবেন না এবং PIN/OTP শেয়ার করবেন না।',note='Warning preview only. No transfer is executed.')
class DemoSend(SenderCheck):
    request_id:str=Field(min_length=8,max_length=80,pattern=r'^[a-zA-Z0-9-]+$')
    warning_acknowledged:bool=False
@app.post('/api/demo-send',dependencies=[Depends(guard)])
def demo_send(c:DemoSend,scope:Literal['batch','replay']='batch'):
    # Separate simulated receipt, without modifying scoring ledger or balances.
    with lock:
        p=product(scope,c.sender_id);check=sender_check(c,scope)
        if check['warning'] and not c.warning_acknowledged:
            raise HTTPException(400,'Review and acknowledge the warning before confirming a demo send.')
        request=dict(sender_id=c.sender_id,recipient_id=c.recipient_id,amount=c.amount)
        receipt=dict(**request,receipt_id='DEMO-'+c.request_id,at=datetime.now(timezone.utc).isoformat(),status='DEMO_COMPLETED',real_funds_moved=False)
        with state['store'].connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS demo_receipts (scope TEXT, request_id TEXT, payload TEXT, PRIMARY KEY(scope,request_id))')
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT payload FROM demo_receipts WHERE scope=? AND request_id=?',(p.scope,c.request_id)).fetchone()
            if previous:
                saved=json.loads(previous[0])
                if any(saved[k]!=v for k,v in request.items()):raise HTTPException(409,'Request ID already belongs to different transfer details.')
                return saved
            db.execute('INSERT INTO demo_receipts VALUES(?,?,?)',(p.scope,c.request_id,json.dumps(receipt)))
            db.execute('INSERT INTO audit(scope,case_id,account_id,action,analyst,note,outcome,at,risk,payload) VALUES(?,?,?,?,?,?,?,?,?,?)',(p.scope,receipt['receipt_id'],c.sender_id,'demo_send','demo-customer','Simulated receipt only; no real funds or balance changes.','demo_completed',receipt['at'],check['recipient_risk'],json.dumps(receipt)))
        return receipt
@app.get('/')
def index():return FileResponse(os.path.join(ROOT,'frontend','index.html'))
