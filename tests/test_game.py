import json
import unittest
from game import Engine, normalize, GRACE, ROOM_TTL, MAX_PLAYERS, MAX_ROOMS
from words import WORDS


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.e = Engine(clock=lambda: self.now)
        self.host = self.e.create(dict(name='Ведущий', rounds=5))
        self.code = self.host['code']
        self.users = [self.host] + [self.e.join(self.code, dict(name=n)) for n in ('Анна', 'Борис')]
        self.r = self.e.rooms[self.code]

    def action(self, user, action, **data):
        return self.e.action(self.code, user['token'], dict(action=action, **data))

    def start(self):
        self.action(self.host, 'start')
        self.guesser = next(u for u in self.users if u['id'] == self.r['guesser'])
        self.helpers = [u for u in self.users if u != self.guesser]
        self.r['word'] = 'самолёт'

    def view(self, user):
        r, p = self.e.auth(self.code, user['token'])
        return self.e.view(r, p)

    def test_normalization(self):
        self.assertEqual(normalize(' ЁЛКА! '), normalize('елка'))
        self.assertEqual(normalize('САМОЛЁТ'), 'самолет')

    def test_secrecy_and_duplicates(self):
        self.start()
        v = self.view(self.guesser)
        self.assertIsNone(v['word'])
        self.assertNotIn('самолёт', json.dumps(v, ensure_ascii=False))
        self.assertNotIn('token', json.dumps(v))
        self.action(self.helpers[0], 'hint', text='Ёлка!')
        self.assertEqual(self.view(self.helpers[0])['hints'][0]['text'], 'Ёлка!')
        self.assertTrue(all(h['text'] is None for h in self.view(self.helpers[1])['hints']))
        self.action(self.helpers[1], 'hint', text='ЕЛКА')
        v = self.view(self.guesser)
        self.assertEqual(v['stage'], 'guess')
        self.assertIsNone(v['word'])
        self.assertTrue(all(h['duplicate'] and h['text'] is None for h in v['hints']))
        v = self.action(self.guesser, 'guess', text='САМОЛЕТ!')
        self.assertEqual((v['word'], v['score']), ('самолёт', 1))
        self.assertTrue(v['result']['correct'])
        self.assertEqual(v['players'][0]['points'], 1)
        self.assertTrue(all(h['text'] for h in v['hints']))
        with self.assertRaises(ValueError): self.action(self.guesser, 'guess', text='самолет')

    def test_unique_hints_and_permissions(self):
        self.start()
        with self.assertRaises(ValueError): self.action(self.helpers[0], 'reset')
        with self.assertRaises(ValueError): self.action(self.guesser, 'hint', text='небо')
        with self.assertRaises(ValueError): self.action(self.helpers[0], 'guess', text='самолет')
        for text in ('самолет!', 'два слова', '123', '<script>', '', 'а'*41, 'о\nк'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.action(self.helpers[0], 'hint', text=text)
        self.action(self.helpers[0], 'hint', text='небо')
        with self.assertRaises(ValueError): self.action(self.helpers[0], 'hint', text='ветер')
        self.action(self.helpers[1], 'hint', text='пилот')
        self.assertEqual([h['text'] for h in self.view(self.guesser)['hints']], ['небо', 'пилот'])
        self.assertIsNone(self.view(self.guesser)['word'])
        self.action(self.guesser, 'pass')
        self.assertEqual(self.r['score'], 0)

    def test_full_game_rotation_no_repeat_and_reset(self):
        seen = []
        for i in range(5):
            self.action(self.host, 'start' if i == 0 else 'next')
            self.assertEqual(self.r['guesser'], self.users[i % 3]['id'])
            seen.append(self.r['word'])
            for u in self.users:
                if u['id'] != self.r['guesser']: self.action(u, 'hint', text='ассоциация')
            u = next(u for u in self.users if u['id'] == self.r['guesser'])
            self.action(u, 'guess', text=self.r['word'])
        self.assertEqual(len(set(seen)), 5)
        self.assertEqual(self.r['score'], 5)
        self.now += 20  # A finished party does not require three online players.
        self.action(self.host, 'next')
        self.assertEqual(self.r['stage'], 'finished')
        self.action(self.host, 'reset')
        self.assertEqual((self.r['stage'], self.r['score'], self.r['history']), ('lobby', 0, []))
        self.assertTrue(all(p['points'] == 0 for p in self.r['players']))

    def test_age_gate_and_modes(self):
        with self.assertRaises(ValueError): self.e.create(dict(name='Имя', mode='adult'))
        adult = self.e.create(dict(name='Имя', mode='adult', adult=True))
        with self.assertRaises(ValueError): self.e.join(adult['code'], dict(name='Друг', adult='true'))
        self.e.join(adult['code'], dict(name='Друг', adult=True))
        for mode in ('easy', 'hard'):
            self.assertEqual(self.e.rooms[self.e.create(dict(name='Имя', mode=mode))['code']]['mode'], mode)
        for mode in ('bogus', [], None):
            with self.assertRaises(ValueError): self.e.create(dict(name='Имя', mode=mode))
        for rounds in (1, '5', 5.0, True):
            with self.assertRaises(ValueError): self.e.create(dict(name='Имя', rounds=rounds))

    def test_reconnect_host_transfer_and_guesser_disconnect(self):
        self.start()
        self.now += 30
        self.view(self.helpers[0])
        self.view(self.helpers[1])
        self.now += 16
        v = self.view(self.helpers[0])
        self.assertEqual(v['host'], self.helpers[0]['id'])
        self.assertEqual(v['stage'], 'reveal')
        self.assertEqual(v['result']['reason'], 'Угадывающий отключился')
        self.assertEqual(self.view(self.guesser)['me'], self.guesser['id'])

    def test_timeout_and_missing_hints(self):
        self.start()
        # Keep presence fresh while advancing the synthetic clock.
        for _ in range(12):
            self.now += 10
            for u in self.users: self.view(u)
        self.assertEqual(self.r['stage'], 'guess')
        for _ in range(9):
            self.now += 10
            for u in self.users: self.view(u)
        self.assertEqual(self.r['stage'], 'reveal')
        self.assertEqual(self.r['result']['reason'], 'Время вышло')
        self.assertEqual(len(self.r['history']), 1)

    def test_lobby_seats_auth_ttl_and_capacity(self):
        with self.assertRaises(ValueError): self.e.join(self.code, dict(name='ВЕДУЩИЙ!'))
        with self.assertRaises(ValueError): self.e.auth(self.code, 'bad')
        with self.assertRaises(ValueError): self.e.auth(self.code, 'ё')
        with self.assertRaises(ValueError): self.action(self.host, 'remove', id=self.host['id'])
        self.action(self.host, 'remove', id=self.users[-1]['id'])
        with self.assertRaises(ValueError): self.view(self.users[-1])
        with self.assertRaises(ValueError): self.action(self.host, 'start')
        for i in range(MAX_PLAYERS - 2): self.e.join(self.code, dict(name='Игрок'+chr(ord('а')+i)))
        with self.assertRaises(ValueError): self.e.join(self.code, dict(name='Лишний'))
        self.now += GRACE + 1
        self.view(self.host)
        self.assertEqual(len(self.r['players']), 1)
        self.now += ROOM_TTL + 1
        with self.assertRaises(ValueError): self.e.room(self.code)

    def test_banks_original_flat_words(self):
        for mode, words in WORDS.items():
            with self.subTest(mode=mode):
                self.assertGreaterEqual(len(words), 100)
                self.assertEqual(len(words), len(set(words)))
                self.assertTrue(all(w.isalpha() and w.islower() for w in words))


if __name__ == '__main__': unittest.main()
