#!/usr/bin/env python3
import os
import json
import time
import base64
import hashlib
import sqlite3
from datetime import datetime
from contextlib import asynccontextmanager

import requests as http_requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.backends import default_backend

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from apscheduler.schedulers.background import BackgroundScheduler

# === Config (env vars or defaults) ===
API_BASE_URL = "https://backendpro.zr66.com"
PRIVATE_KEY_B64 = os.environ.get("ZR_PRIVATE_KEY", "MIIBVAIBADANBgkqhkiG9w0BAQEFAASCAT4wggE6AgEAAkEAg8V2L+rhNAdcxt+LbYV4Y9lHDsLqJk7HEuyaAfRqRyZY7gYE6UbxgTHAmbs9PMLIsGyivKO3BLzyw6HzbMgKiwIDAQABAkA5fPyDC0YVHOEtInoB3ikX5sNJfWAKNnRDnVXTZH65ay9fh/1Hwhrc10tnHcj31TykODejvasSWHVXE7Ezq92BAiEA1fYk1SizxFSg2R60dlduagLAAVNrin9qI+xXxnE8MzcCIQCdqU8X1KLpR59MolcAAUfdzkscEzfBOKZCBg3KWx/1TQIhALYvjVVj/w5h8URvfMJ32DC0fsGiQqP/smU8TdFPgi8pAiByNR1YU+4XMozQxKBlHohiwndiRQGUdGbrWNtQhKYn2QIgUv3SsItetsk+J2Whn+dHOHbajPeF2DtZh76YLgtreNg=")
LOGIN_EMAIL = os.environ.get("ZR_EMAIL", "myuw0856@hotmail.com")
LOGIN_PASSWORD = os.environ.get("ZR_PASSWORD", "")
DEVICE_ID = os.environ.get("ZR_DEVICE_ID", "17b080c5756f2ffd")
DEVICE_NAME = os.environ.get("ZR_DEVICE_NAME", "HONOR-BVL-AN20")
DEVICE_MODEL = os.environ.get("ZR_DEVICE_MODEL", "BVL-AN20")
STOCK_CODE = os.environ.get("ZR_STOCK_CODE", "07666")

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "monitor.db")
TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

templates = Jinja2Templates(directory=TEMPLATES_DIR)

# === Minimal API Client ===
_private_key: RSAPrivateKey | None = None
_token: str | None = None
_user_id: str | None = None


def _load_private_key():
    global _private_key
    key_bytes = base64.b64decode(PRIVATE_KEY_B64)
    _private_key = serialization.load_der_private_key(key_bytes, password=None, backend=default_backend())


def _sign_request(data: dict) -> dict:
    sign_data = dict(data)
    sign_data.pop('sign', None)
    sign_data['timeStamp'] = int(time.time() * 1000)
    sorted_json = json.dumps(sign_data, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    signature = _private_key.sign(sorted_json.encode('utf-8'), padding.PKCS1v15(), hashes.SHA1())
    sign_data['sign'] = base64.b64encode(signature).decode('utf-8')
    return sign_data


def _get_headers():
    headers = {
        "Content-Type": "application/json; charset=UTF-8",
        "lang": "zh_CN",
        "osType": "android",
        "osVersion": "14",
        "appVersion": "6.0.0(600)",
        "deviceId": DEVICE_ID,
        "deviceName": DEVICE_NAME,
        "deviceModel": DEVICE_MODEL,
    }
    if _token:
        headers["token"] = _token
    if _user_id:
        headers["userId"] = _user_id
    return headers


def do_login():
    global _token, _user_id
    if not LOGIN_PASSWORD:
        print("[WARN] ZR_PASSWORD not set, skip login")
        return False
    data = {
        "accountType": 1,
        "loginEmail": LOGIN_EMAIL,
        "loginPassword": hashlib.md5(LOGIN_PASSWORD.encode()).hexdigest(),
        "type": 2
    }
    signed = _sign_request(data)
    headers = _get_headers()
    try:
        resp = http_requests.post(f"{API_BASE_URL}/as_user/api/user_account/v1/user_login_pwd", json=signed, headers=headers, timeout=30)
        result = resp.json()
        if result.get('code') == '000000':
            _token = result['data']['token']
            _user_id = result['data']['userId']
            print(f"[LOGIN] 登录成功: {LOGIN_EMAIL}")
            return True
        else:
            print(f"[LOGIN] 登录失败: {result.get('msg')}")
            return False
    except Exception as e:
        print(f"[LOGIN] 异常: {e}")
        return False


def get_ipo_list() -> list:
    global _token, _user_id
    signed = _sign_request({})
    headers = _get_headers()
    try:
        resp = http_requests.post(f"{API_BASE_URL}/as_trade/api/ipo/v1/subscribe_list", json=signed, headers=headers, timeout=30)
        result = resp.json()
        code = result.get('code', '')
        if code == '000000':
            return result.get('data', [])
        if code in ('000102', '000112'):
            print("[TOKEN] Token失效，重新登录...")
            if do_login():
                signed = _sign_request({})
                headers = _get_headers()
                resp = http_requests.post(f"{API_BASE_URL}/as_trade/api/ipo/v1/subscribe_list", json=signed, headers=headers, timeout=30)
                result = resp.json()
                if result.get('code') == '000000':
                    return result.get('data', [])
        print(f"[API] 查询失败: {result.get('msg')}")
        return []
    except Exception as e:
        print(f"[API] 异常: {e}")
        return []


# === Database ===
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS quota_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME NOT NULL,
            balance REAL NOT NULL,
            stop_flag TEXT,
            lots_remaining INTEGER,
            change_amount REAL
        )
    """)
    conn.commit()
    conn.close()


def get_last_balance():
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT balance FROM quota_history ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return row[0] if row else None


def collect_quota():
    try:
        ipo_list = get_ipo_list()
        if not ipo_list:
            print(f"[{datetime.now()}] IPO列表为空")
            return

        for ipo in ipo_list:
            if ipo.get('code') == STOCK_CODE:
                balance = float(ipo.get('compFinancingBalance', 0))
                stop_flag = str(ipo.get('stopFinancingFlag', ''))
                lots_remaining = int(balance / 4725)

                last_balance = get_last_balance()
                change_amount = balance - last_balance if last_balance is not None else 0

                conn = sqlite3.connect(DB_PATH)
                conn.execute(
                    "INSERT INTO quota_history (timestamp, balance, stop_flag, lots_remaining, change_amount) VALUES (?, ?, ?, ?, ?)",
                    (datetime.now().isoformat(), balance, stop_flag, lots_remaining, change_amount)
                )
                conn.commit()
                conn.close()
                print(f"[{datetime.now()}] {STOCK_CODE}: {balance:,.2f} HKD | 变化: {change_amount:+,.2f}")
                return

        print(f"[{datetime.now()}] 未找到 {STOCK_CODE}")
    except Exception as e:
        print(f"[{datetime.now()}] 采集异常: {e}")


# === FastAPI App ===
scheduler = BackgroundScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _load_private_key()
    init_db()
    if LOGIN_PASSWORD:
        do_login()
    collect_quota()
    scheduler.add_job(collect_quota, 'interval', minutes=1, id='quota_collector')
    scheduler.start()
    print(f"监控启动: {STOCK_CODE} | 每分钟采集")
    yield
    scheduler.shutdown()


app = FastAPI(lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/data")
async def get_data(limit: int = Query(default=500, le=5000)):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT timestamp, balance, stop_flag, lots_remaining, change_amount FROM quota_history ORDER BY id DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return {"data": [dict(row) for row in reversed(rows)], "count": len(rows)}


@app.get("/api/latest")
async def get_latest():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT timestamp, balance, stop_flag, lots_remaining, change_amount FROM quota_history ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row else {"error": "no data"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
