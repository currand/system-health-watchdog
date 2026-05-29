#!/usr/bin/env python3
"""Tiny HTTP server at port 19876 for self-healing pipeline testing.
   Start: python3 scripts/test-server.py &
   Health: http://localhost:19876/health → {"status":"ok"}
   Stop: kill $(cat /tmp/self-heal-test.pid)
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import json, os, signal

PORT = 19876
PIDFILE = "/tmp/self-heal-test.pid"

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({"status": "ok", "service": "self-heal-test"}).encode())
    def log_message(self, *a): pass

if __name__ == "__main__":
    server = HTTPServer(("", PORT), Handler)
    with open(PIDFILE, "w") as f:
        f.write(str(os.getpid()))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        if os.path.exists(PIDFILE):
            os.unlink(PIDFILE)