import os
import json
import time
import base64
import hashlib
from datetime import datetime
from http.server import BaseHTTPRequestHandler

import requests as http_requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend
from upstash_redis import Redis

API_BASE_URL = "https://backendpro.zr66.com"
PRIVATE_KEY_B64 = os.environ.get("ZR_PRIVATE_KEY", "MIIBVAIBADANBgkqhkiG9w0BAQEFAASCAT4wggE6AgEAAkEAg8V2L+rhNAdcxt+LbYV4Y9lHDsLqJk7HEuyaAfRqRyZY7gYE6UbxgTHAmbs9PMLIsGyivKO3BLzyw6HzbMgKiwIDAQABAkA5fPyDC0YVHOEtInoB3ikX5sNJfWAKNnRDnVXTZH65ay9fh/1Hwhrc10tnHcj31TykODejvasSWHVXE7Ezq92BAiEA1fYk1SizxFSg2R60dlduagLAAVNrin9qI+xXxnE8MzcCIQCdqU8X1KLpR59MolcAAUfdzkscEzfBOKZCBg3KWx/1TQIhALYvjVVj/w5h8URvfMJ32DC0fsGiQqP/smU8TdFPgi8pAiByNR1YU+4XMozQxKBlHohiwndiRQGUdGbrWNtQhKYn2QIgUv3SsItetsk+J2Whn+dHOHbajPeF2DtZh76YLgtreNg=")
LOGIN_EMAIL = os.environ.get("ZR_EMAIL", "zxup5809@hotmail.com")
LOGIN_PASSWORD = os.environ.get("ZR_PASSWORD", "")
DEVICE_ID = os.environ.get("ZR_DEVICE_ID", "799476863a6d4470")
DEVICE_NAME = os.environ.get("ZR_DEVICE_NAME", "vivo-V2324HA")
DEVICE_MODEL = os.environ.get("ZR_DEVICE_MODEL", "V2324HA")
STOCK_CODE = os.environ.get("ZR_STOCK_CODE", "07666")

UPSTASH_REDIS_REST_URL = os.environ.get("KV_REST_API_URL", "")
UPSTASH_REDIS_REST_TOKEN = os.environ.get("KV_REST_API_TOKEN", "")


def sign_request(data, private_key):
    sign_data = dict(data)
    sign_data.pop('sign', None)
    sign_data['timeStamp'] = int(time.time() * 1000)
    sorted_json = json.dumps(sign_data, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    signature = private_key.sign(sorted_json.encode('utf-8'), padding.PKCS1v15(), hashes.SHA1())
    sign_data['sign'] = base64.b64encode(signature).decode('utf-8')
    return sign_data


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            result = self.collect()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def collect(self):
        key_bytes = base64.b64decode(PRIVATE_KEY_B64)
        private_key = serialization.load_der_private_key(key_bytes, password=None, backend=default_backend())

        headers = {
            "Content-Type": "application/json; charset=UTF-8",
            "lang": "zh_CN", "osType": "android", "osVersion": "14",
            "appVersion": "6.0.0(600)",
            "deviceId": DEVICE_ID, "deviceName": DEVICE_NAME, "deviceModel": DEVICE_MODEL,
        }

        login_data = sign_request({
            "accountType": 1, "loginEmail": LOGIN_EMAIL,
            "loginPassword": hashlib.md5(LOGIN_PASSWORD.encode()).hexdigest(), "type": 2
        }, private_key)
        resp = http_requests.post(f"{API_BASE_URL}/as_user/api/user_account/v1/user_login_pwd", json=login_data, headers=headers, timeout=10)
        result = resp.json()
        if result.get('code') != '000000':
            return {"status": "error", "msg": f"login failed: {result.get('msg')}"}

        headers['token'] = result['data']['token']
        headers['userId'] = result['data']['userId']

        ipo_data = sign_request({}, private_key)
        resp2 = http_requests.post(f"{API_BASE_URL}/as_trade/api/ipo/v1/subscribe_list", json=ipo_data, headers=headers, timeout=10)
        r2 = resp2.json()
        if r2.get('code') != '000000':
            return {"status": "error", "msg": f"query failed: {r2.get('msg')}"}

        for ipo in r2.get('data', []):
            if ipo.get('code') == STOCK_CODE:
                balance = float(ipo.get('compFinancingBalance', 0))
                stop_flag = str(ipo.get('stopFinancingFlag', ''))
                now = datetime.now().isoformat()

                redis = Redis(url=UPSTASH_REDIS_REST_URL, token=UPSTASH_REDIS_REST_TOKEN)

                last = redis.lindex("quota:history", -1)
                last_balance = json.loads(last).get("balance", 0) if last else 0
                change = balance - last_balance if last_balance else 0

                record = json.dumps({"t": now, "b": balance, "s": stop_flag, "c": change})
                redis.rpush("quota:history", record)
                redis.ltrim("quota:history", -1440, -1)

                return {"status": "ok", "balance": balance, "change": change}

        return {"status": "error", "msg": f"{STOCK_CODE} not found"}
