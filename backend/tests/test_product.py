import os
import pytest
from fastapi.testclient import TestClient
from backend.app.main import app, state
from backend.app.product import Store, Product

@pytest.fixture(scope='module')
def client(tmp_path_factory):
    path=str(tmp_path_factory.mktemp('store')/'audit.sqlite3')
    old=os.environ.get('SENTINEL_DB');os.environ['SENTINEL_DB']=path
    old_auth=os.environ.get('SENTINEL_AUTH_DB');os.environ['SENTINEL_AUTH_DB']=path+'.auth'
    with TestClient(app) as c:yield c
    if old_auth is None:os.environ.pop('SENTINEL_AUTH_DB',None)
    else:os.environ['SENTINEL_AUTH_DB']=old_auth
    if old is None:os.environ.pop('SENTINEL_DB',None)
    else:os.environ['SENTINEL_DB']=old

def test_positive_amount_and_unknown_ids(client):
    for amt in (0,-1,1000001):
        assert client.post('/api/check-cashout',json={'account_id':0,'amount':amt}).status_code==422
    assert client.post('/api/check-cashout',json={'account_id':99999,'amount':10}).status_code==404

def test_release_persists_and_is_single_use(client):
    i=client.get('/api/alerts').json()[0]['id']
    client.post(f'/api/alerts/{i}/decision',json={'action':'release','analyst':'test','note':'Reviewed','outcome':'legitimate'})
    store=Store(state['store'].path);p=Product(state['s'],store)
    assert p.status(i)=='release'
    assert client.post('/api/check-cashout',json={'account_id':i,'amount':100}).json()['release_consumed']
    assert client.post('/api/check-cashout',json={'account_id':i,'amount':100}).json()['decision']=='HOLD_AND_VERIFY'
    assert any(x['note']=='Reviewed' for x in store.history('batch'))

def test_replay_is_frozen_and_no_future_cashout(client):
    r=client.post('/api/replay/reset',json={'scenario':'scam'}).json()
    model=state['replay'].model.iso
    assert r['events']==[] and r['impact']['observed_fraud_cashout_requests']==0
    for _ in range(4):r=client.post('/api/replay/step',json={}).json()
    assert model is state['replay'].model.iso
    assert not r['checks']
    risk=float(state['replay'].model.F.risk[state['replay'].m1])
    r=client.post('/api/replay/step',json={}).json()
    assert r['checks'][0]['pre_risk']==risk
    assert len(state['replay'].model.t)==state['replay'].cut+5
    prior_scope=r['scope']
    client.post(f"/api/alerts/{r['focus']}/decision?scope=replay",json={'action':'escalate'})
    client.post('/api/replay/reset',json={'scenario':'legitimate'})
    assert state['store'].history(prior_scope)

def test_new_evidence_invalidates_release(client):
    r=client.post('/api/replay/reset',json={'scenario':'scam'}).json();i=r['focus']
    client.post(f'/api/alerts/{i}/decision?scope=replay',json={'action':'release'})
    old=state['replay'].product.case(i)
    client.post('/api/replay/step',json={})
    assert state['replay'].product.case(i)!=old
    assert state['replay'].product.status(i)!='release'

def test_security_warning_and_locations(client,monkeypatch):
    monkeypatch.setenv('SENTINEL_API_KEY','test-secret')
    assert client.post('/api/replay/step',json={}).status_code==401
    assert client.post('/api/replay/step',json={},headers={'X-API-Key':'test-secret'}).status_code==200
    token=client.post('/api/demo-auth/login',json={'wallet_id':0,'pin':'24680'},headers={'X-API-Key':'test-secret'}).json()['session_token']
    r=client.post('/api/sender-check',json={'session_token':token,'sender_id':0,'recipient_id':1,'amount':57000},headers={'X-API-Key':'test-secret'})
    assert r.status_code==200 and 'warning' in r.json()
    d=client.get('/api/alerts/1').json()
    assert 'lat' in d['location'] and 'contributions' in d
    assert len(client.get('/api/hotspots').json())==8

def test_fast_ring_can_pause_before_cashout_and_slow_ring_is_not_overclaimed(client):
    r=client.post('/api/replay/reset',json={'scenario':'scam'}).json()
    while not r['done']:r=client.post('/api/replay/step',json={}).json()
    assert r['impact']['paused_before_cashout']==53700
    assert all(x['decision']=='HOLD_AND_VERIFY' for x in r['checks'])
    r=client.post('/api/replay/reset',json={'scenario':'legitimate'}).json()
    while not r['done']:r=client.post('/api/replay/step',json={}).json()
    assert r['impact']['legitimate_requests_paused']==0


def test_demo_send_warning_idempotency_and_separate_ledger(client):
    token=client.post('/api/demo-auth/login',json={'wallet_id':0,'pin':'24680'}).json()['session_token']
    payload={'session_token':token,'sender_id':0,'recipient_id':1,'amount':57000,'request_id':'test-demo-receipt'}
    count=len(state['s'].t)
    assert client.post('/api/demo-send',json=payload).status_code==400
    payload['warning_acknowledged']=True
    receipt=client.post('/api/demo-send',json=payload)
    assert receipt.status_code==200 and receipt.json()['real_funds_moved'] is False
    assert client.post('/api/demo-send',json=payload).json()==receipt.json()
    payload['amount']=58000
    assert client.post('/api/demo-send',json=payload).status_code==409
    events=[r for r in client.get('/api/audit').json() if r['action']=='demo_send']
    assert len(events)==1 and len(state['s'].t)==count


def test_demo_endpoints_require_wallet_session(client):
    payload={'sender_id':0,'recipient_id':1,'amount':100,'request_id':'unauthorized-demo-send'}
    assert client.post('/api/sender-check',json=payload).status_code==401
    assert client.post('/api/demo-send',json=payload).status_code==401
    token=client.post('/api/demo-auth/login',json={'wallet_id':1,'pin':'24680'}).json()['session_token']
    payload['session_token']=token
    assert client.post('/api/demo-send',json=payload).status_code==401
    assert client.post('/api/demo-auth/logout',json={'session_token':token}).status_code==200
    assert not state['auth'].verify(1,token)
