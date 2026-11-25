"""Small bounded JSON/HTTP service. No external runtime dependencies."""
import collections
import json
import os
from pathlib import Path
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from game import Engine

ROOT = Path(__file__).parent / 'static'
ENGINE = Engine()
LOCK = threading.RLock()
RATE = collections.OrderedDict()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64
    # A slow client cannot allocate unbounded HTTP handler threads.
    slots = threading.BoundedSemaphore(64)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version = 'Jone'

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, format, *args):
        # No tokens, nicknames, words or room IDs in application logs.
        pass

    def send(self, status, body, content_type='application/json; charset=utf-8'):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def limited(self, join=False):
        # Deliberately use socket peer, NOT spoofable forwarded headers. Cluster ingress
        # peers share a generous aggregate budget; create/join has a separate budget.
        key = (self.client_address[0], join)
        now = time.monotonic()
        with LOCK:
            bucket = RATE.setdefault(key, collections.deque())
            RATE.move_to_end(key)
            while bucket and now - bucket[0] > 60:
                bucket.popleft()
            if len(bucket) >= (120 if join else 6000):
                return True
            bucket.append(now)
            while len(RATE) > 4096:
                RATE.popitem(last=False)
        return False

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/healthz':
            return self.send(200, dict(ok=True, version=os.getenv('APP_VERSION', 'local')))
        if self.limited():
            return self.send(429, dict(error='Слишком много запросов. Подождите минуту'))
        if re.fullmatch(r'/api/rooms/[A-Fa-f0-9]{8}', path):
            token = self.headers.get('Authorization', '').removeprefix('Bearer ')
            try:
                with LOCK:
                    r, p = ENGINE.auth(path.split('/')[-1], token)
                    result = ENGINE.view(r, p)
                return self.send(200, result)
            except ValueError as e:
                return self.send(400, dict(error=str(e)))
        files = {'/': ('index.html', 'text/html; charset=utf-8'),
                 '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                 '/style.css': ('style.css', 'text/css; charset=utf-8'),
                 '/icon.svg': ('icon.svg', 'image/svg+xml')}
        if path in files:
            name, mime = files[path]
            return self.send(200, (ROOT / name).read_bytes(), mime)
        self.send(404, dict(error='Не найдено'))

    def do_POST(self):
        path = urlsplit(self.path).path
        origin = self.headers.get('Origin')
        if origin and (urlsplit(origin).netloc != self.headers.get('Host') or urlsplit(origin).scheme not in ('http', 'https')):
            return self.send(403, dict(error='Чужой источник запроса'))
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.send(415, dict(error='Нужен JSON'))
        joining = path == '/api/rooms' or path.endswith('/join')
        if self.limited(joining):
            return self.send(429, dict(error='Слишком много запросов. Подождите минуту'))
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if size < 2 or size > 8192:
                return self.send(413, dict(error='Слишком большой запрос'))
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError('Нужен объект JSON')
            with LOCK:
                if path == '/api/rooms':
                    result = ENGINE.create(data)
                elif re.fullmatch(r'/api/rooms/[A-Fa-f0-9]{8}/join', path):
                    result = ENGINE.join(path.split('/')[-2], data)
                elif re.fullmatch(r'/api/rooms/[A-Fa-f0-9]{8}/action', path):
                    token = self.headers.get('Authorization', '').removeprefix('Bearer ')
                    result = ENGINE.action(path.split('/')[-2], token, data)
                else:
                    return self.send(404, dict(error='Не найдено'))
            self.send(200, result)
        except (ValueError, TypeError, KeyError, UnicodeDecodeError):
            # Preserve safe, actionable engine validation messages only.
            import sys
            error = sys.exception()
            text = str(error) if type(error) is ValueError else 'Некорректный запрос'
            self.send(400, dict(error=text))


if __name__ == '__main__':
    print('Jone listening on :8080', flush=True)
    Server(('0.0.0.0', int(os.getenv('PORT', '8080'))), Handler).serve_forever()
