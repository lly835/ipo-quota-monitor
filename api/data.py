import os
import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from upstash_redis import Redis

UPSTASH_REDIS_REST_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "")
UPSTASH_REDIS_REST_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        limit = min(int(query.get("limit", [500])[0]), 1440)

        redis = Redis(url=UPSTASH_REDIS_REST_URL, token=UPSTASH_REDIS_REST_TOKEN)
        raw_list = redis.lrange("quota:history", -limit, -1)

        data = []
        for item in raw_list:
            rec = json.loads(item) if isinstance(item, str) else item
            data.append({
                "timestamp": rec["t"],
                "balance": rec["b"],
                "stop_flag": rec.get("s", ""),
                "change_amount": rec.get("c", 0)
            })

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps({"data": data, "count": len(data)}).encode())
