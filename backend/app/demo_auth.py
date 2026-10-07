"""Synthetic wallet authentication. Never use real upay credentials here."""
import hashlib
import hmac
import secrets
import sqlite3
import time

DEMO_PIN = '24680'
LOCK_SECONDS = 24 * 60 * 60
SESSION_SECONDS = 15 * 60

class DemoAuth:
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS demo_pin_accounts (wallet INTEGER PRIMARY KEY, salt TEXT NOT NULL, digest TEXT NOT NULL, failures INTEGER NOT NULL DEFAULT 0, locked_until REAL NOT NULL DEFAULT 0)')
            db.execute('CREATE TABLE IF NOT EXISTS demo_pin_sessions (digest TEXT PRIMARY KEY, wallet INTEGER NOT NULL, expires REAL NOT NULL)')

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    @staticmethod
    def pin_hash(pin, salt):
        return hashlib.pbkdf2_hmac('sha256', pin.encode(), bytes.fromhex(salt), 200000).hex()

    @staticmethod
    def token_hash(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def account(self, db, wallet, now):
        row = db.execute('SELECT salt,digest,failures,locked_until FROM demo_pin_accounts WHERE wallet=?', (wallet,)).fetchone()
        if row is None:
            salt = secrets.token_hex(16)
            digest = self.pin_hash(DEMO_PIN, salt)
            db.execute('INSERT INTO demo_pin_accounts(wallet,salt,digest) VALUES(?,?,?)', (wallet,salt,digest))
            row = (salt,digest,0,0)
        if row[3] and row[3] <= now:
            db.execute('UPDATE demo_pin_accounts SET failures=0,locked_until=0 WHERE wallet=?', (wallet,))
            row = (row[0],row[1],0,0)
        return row

    def status(self, wallet):
        now = time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self.account(db,wallet,now)
            return dict(locked=row[3]>now,remaining_seconds=max(0,int(row[3]-now+0.999)),attempts_remaining=max(0,3-row[2]),locked_until=row[3])

    def login(self, wallet, pin):
        now = time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self.account(db,wallet,now)
            if row[3] > now:
                return dict(ok=False,locked=True,remaining_seconds=int(row[3]-now+0.999),attempts_remaining=0,message='Demo wallet locked for 24 hours after 3 incorrect PIN attempts.')
            if not hmac.compare_digest(self.pin_hash(pin,row[0]),row[1]):
                failures = row[2]+1
                until = now+LOCK_SECONDS if failures >= 3 else 0
                db.execute('UPDATE demo_pin_accounts SET failures=?,locked_until=? WHERE wallet=?',(failures,until,wallet))
                if until: db.execute('DELETE FROM demo_pin_sessions WHERE wallet=?',(wallet,))
                return dict(ok=False,locked=bool(until),remaining_seconds=LOCK_SECONDS if until else 0,attempts_remaining=max(0,3-failures),message='Demo wallet locked for 24 hours.' if until else 'Incorrect demo PIN.')
            db.execute('UPDATE demo_pin_accounts SET failures=0,locked_until=0 WHERE wallet=?',(wallet,))
            db.execute('DELETE FROM demo_pin_sessions WHERE expires<=?',(now,))
            token = secrets.token_urlsafe(32)
            db.execute('INSERT INTO demo_pin_sessions VALUES(?,?,?)',(self.token_hash(token),wallet,now+SESSION_SECONDS))
            return dict(ok=True,session_token=token,expires_in=SESSION_SECONDS,wallet_id=wallet,message='Demo wallet unlocked.')

    def verify(self, wallet, token):
        now = time.time()
        with self.connect() as db:
            row = db.execute('SELECT locked_until FROM demo_pin_accounts WHERE wallet=?',(wallet,)).fetchone()
            if row and row[0]>now: return False
            session = db.execute('SELECT wallet,expires FROM demo_pin_sessions WHERE digest=?',(self.token_hash(token or ''),)).fetchone()
            return bool(session and session[0]==wallet and session[1]>now)

    def logout(self, token):
        with self.connect() as db:
            db.execute('DELETE FROM demo_pin_sessions WHERE digest=?',(self.token_hash(token or ''),))
