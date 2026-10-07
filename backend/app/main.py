import os
from contextlib import asynccontextmanager
from threading import RLock
from typing import Literal, Optional
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from .scoring import DATA, ROOT, Sentinel
from .product import Product, Store, Replay
state={};lock=RLock()
@asynccontextmanager
async def lifespan(app):
    if not os.path.exists(os.path.join(DATA,'transactions.csv')):
        from data.generate_data import main
        main()
    s=Sentinel();store=Store(os.getenv('SENTINEL_DB',os.path.join(DATA,'sentinel.sqlite3')))
    state.update(s=s,store=store,p=Product(s,store),replay=Replay(s,store))
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
@app.post('/api/sender-check',dependencies=[Depends(guard)])
def sender_check(c:SenderCheck,scope:Literal['batch','replay']='batch'):
    with lock:
        p=product(scope,c.sender_id);product(scope,c.recipient_id)
        if c.sender_id==c.recipient_id:raise HTTPException(400,'Sender and recipient must differ.')
        if p.s.F.role[c.sender_id]!='customer' or p.s.F.role[c.recipient_id]!='customer':raise HTTPException(400,'Use customer wallets.')
        known=bool(((p.s.t.src==c.sender_id)&(p.s.t.dst==c.recipient_id)&(p.s.t.type=='send_money')).any())
        risk=float(p.s.F.risk[c.recipient_id]);warn=risk>=60 or (not known and c.amount>=15000)
        return dict(warning=warn,recipient_risk=risk,new_recipient=not known,message='Pause and verify who you are paying. Never send money for a prize or share your PIN/OTP.' if warn else 'Check the recipient and amount before sending. A low score does not guarantee safety.',message_bn='পাঠানোর আগে প্রাপকের পরিচয় যাচাই করুন। পুরস্কারের জন্য টাকা পাঠাবেন না এবং PIN/OTP শেয়ার করবেন না।',note='Warning preview only. No transfer is executed.')
@app.get('/')
def index():return FileResponse(os.path.join(ROOT,'frontend','index.html'))
