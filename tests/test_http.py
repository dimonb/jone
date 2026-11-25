import http.client
import json
import threading
import unittest
import server
from game import Engine


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = server.Server(('127.0.0.1', 0), server.Handler)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()

    def setUp(self):
        with server.LOCK:
            server.ENGINE = Engine()
            server.RATE.clear()

    def request(self, path, data=None, headers=None, raw=None):
        h = {'Content-Type': 'application/json', **(headers or {})}
        c = http.client.HTTPConnection(*self.http.server_address, timeout=3)
        c.request('POST' if data is not None or raw is not None else 'GET', path,
                  body=raw if raw is not None else json.dumps(data) if data is not None else None,
                  headers=h)
        r = c.getresponse()
        result = r.status, dict(r.getheaders()), r.read()
        c.close()
        return result

    def api(self, path, data=None, token=None):
        status, _, body = self.request(path, data, {'Authorization': 'Bearer '+token} if token else {})
        self.assertEqual(status, 200, body)
        return json.loads(body)

    def test_static_and_security_headers(self):
        for path in ('/', '/room/ABCDEF12', '/room/abcdef12/', '/style.css', '/app.js', '/icon.svg', '/healthz'):
            status, headers, body = self.request(path)
            self.assertEqual(status, 200)
            self.assertTrue(body)
            self.assertEqual(headers['X-Frame-Options'], 'DENY')
            self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertIn("script-src 'self'", headers['Content-Security-Policy'])
        self.assertEqual(self.request('/../game.py')[0], 404)
        self.assertEqual(self.request('/words.py')[0], 404)
        for path in ('/room/ABCDEF12/token', '/room/invalid', '/room/../game.py'):
            self.assertEqual(self.request(path)[0], 404)
        self.assertEqual(self.request('/room/ABCDEF12')[2], self.request('/')[2])
        self.assertEqual(self.request('/api/rooms/ABCDEF12')[0], 400)

    def test_real_http_game_and_reconnect(self):
        a = self.api('/api/rooms', dict(name='Аня'))
        root = '/api/rooms/'+a['code']
        b = self.api(root+'/join', dict(name='Боря'))
        c = self.api(root+'/join', dict(name='Вера'))
        v = self.api(root+'/action', dict(action='start'), a['token'])
        self.assertIsNone(v['word'])
        word = self.api(root, token=b['token'])['word']
        self.api(root+'/action', dict(action='hint', text='подсказка'), b['token'])
        self.api(root+'/action', dict(action='hint', text='ассоциация'), c['token'])
        self.assertIsNone(self.api(root, token=a['token'])['word'])
        v = self.api(root+'/action', dict(action='guess', text=word), a['token'])
        self.assertEqual(v['score'], 1)
        self.assertEqual(self.api(root, token=a['token'])['me'], a['id'])
        self.assertNotIn('token', json.dumps(v))
        self.assertEqual(self.request(root)[0], 400)

    def test_validation_origin_size_and_age(self):
        for raw in ('{x', '[]', 'null', '{"mode":[]}'):
            self.assertEqual(self.request('/api/rooms', raw=raw)[0], 400)
        self.assertEqual(self.request('/api/rooms', raw='x'*8193)[0], 413)
        self.assertEqual(self.request('/api/rooms', {}, {'Content-Type':'text/plain'})[0], 415)
        self.assertEqual(self.request('/api/rooms', {}, {'Origin':'https://evil.invalid'})[0], 403)
        self.assertEqual(self.request('/api/rooms', dict(name='Имя', mode='adult'))[0], 400)
        a=self.api('/api/rooms', dict(name='Имя', mode='adult', adult=True))
        self.assertEqual(self.request('/api/rooms/'+a['code']+'/join', dict(name='Друг'))[0], 400)
        self.assertEqual(self.request('/unknown', {})[0], 404)

    def test_join_rate_limit(self):
        with server.LOCK:
            server.RATE[('127.0.0.1', True)] = server.collections.deque([server.time.monotonic()]*120)
        self.assertEqual(self.request('/api/rooms', dict(name='Имя'))[0], 429)
        self.assertEqual(self.request('/healthz')[0], 200)


if __name__ == '__main__': unittest.main()
