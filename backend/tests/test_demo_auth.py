import importlib.util
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
spec=importlib.util.spec_from_file_location('demo_auth',Path(__file__).parents[1]/'app/demo_auth.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def test_lock_persists_and_expires(tmp_path,monkeypatch):
    now=[1000.0];monkeypatch.setattr(m.time,'time',lambda:now[0])
    path=str(tmp_path/'auth.sqlite');a=m.DemoAuth(path)
    token=a.login(1,'24680')['session_token']
    assert a.verify(1,token) and not a.verify(2,token)
    assert a.login(1,'00000')['attempts_remaining']==2
    assert a.login(1,'00000')['attempts_remaining']==1
    r=a.login(1,'00000');assert r['locked'] and r['remaining_seconds']==86400
    b=m.DemoAuth(path)
    assert b.status(1)['locked'] and not b.verify(1,token)
    assert not b.login(1,'24680')['ok']
    now[0]+=86399;assert b.status(1)['locked']
    now[0]+=1;assert b.login(1,'24680')['ok']
    assert not b.verify(1,token)

def test_success_reset_sessions_and_wallet_isolation(tmp_path,monkeypatch):
    now=[1000.0];monkeypatch.setattr(m.time,'time',lambda:now[0])
    a=m.DemoAuth(str(tmp_path/'auth.sqlite'))
    a.login(1,'00000');r=a.login(1,'24680');assert a.status(1)['attempts_remaining']==3
    token=r['session_token'];assert not a.verify(1,'forged')
    now[0]+=900;assert not a.verify(1,token)
    r=a.login(1,'24680');a.logout(r['session_token']);assert not a.verify(1,r['session_token'])
    for _ in range(3):a.login(1,'00000')
    assert a.login(2,'24680')['ok']

def test_parallel_wrong_attempts_are_counted(tmp_path):
    a=m.DemoAuth(str(tmp_path/'auth.sqlite'))
    with ThreadPoolExecutor(max_workers=3) as pool:
        results=list(pool.map(lambda _:a.login(5,'00000'),range(3)))
    assert sorted(r['attempts_remaining'] for r in results)==[0,1,2]
    assert a.status(5)['locked']
