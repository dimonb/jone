"""Single-process room engine. All access is serialized by HTTP's engine lock."""
import collections
import re
import secrets
import time
import unicodedata
from words import WORDS

MIN_PLAYERS, MAX_PLAYERS, MAX_ROOMS = 3, 12, 200
PRESENCE, GRACE, ROOM_TTL = 15, 45, 6 * 3600
HINT_TIME, GUESS_TIME = 120, 90


def normalize(value):
    value = unicodedata.normalize('NFKC', value).lower().replace('ё', 'е')
    return ''.join(c for c in value if c.isalpha())


def clean(value, maximum):
    if not isinstance(value, str):
        raise ValueError('Нужен текст')
    value = value.strip()
    if not value or len(value) > maximum or any(unicodedata.category(c).startswith('C') for c in value):
        raise ValueError('Проверьте длину и символы')
    return value


class Engine:
    def __init__(self, clock=time.time):
        self.rooms = {}
        self.clock = clock

    def create(self, data):
        self.cleanup()
        if len(self.rooms) >= MAX_ROOMS:
            raise ValueError('Все столы заняты. Попробуйте позже')
        mode = data.get('mode', 'easy')
        if not isinstance(mode, str) or mode not in WORDS or type(data.get('rounds', 10)) is not int or data.get('rounds', 10) not in (5, 10, 13):
            raise ValueError('Неизвестный режим или число раундов')
        if mode == 'adult' and data.get('adult') is not True:
            raise ValueError('Подтвердите, что вам исполнилось 18 лет')
        code = secrets.token_hex(4).upper()
        while code in self.rooms:
            code = secrets.token_hex(4).upper()
        r = dict(code=code, mode=mode, total=data.get('rounds', 10), players=[],
                 host=None, stage='lobby', number=0, score=0, updated=self.clock(),
                 history=[], used=[], deadline=None, word=None, guesser=None,
                 participants=[], hints={}, guess='', result=None)
        self.rooms[code] = r
        try:
            return self.join(code, data)
        except ValueError:
            del self.rooms[code]
            raise

    def room(self, code):
        self.cleanup()
        r = self.rooms.get(code.upper())
        if not r:
            raise ValueError('Комната не найдена или уже закрыта')
        self.tick(r)
        return r

    def join(self, code, data):
        r = self.room(code)
        if r['mode'] == 'adult' and data.get('adult') is not True:
            raise ValueError('Подтвердите, что вам исполнилось 18 лет')
        if r['stage'] != 'lobby':
            raise ValueError('Игра уже идёт. Попросите ведущего вернуться в лобби')
        if len(r['players']) >= MAX_PLAYERS:
            raise ValueError('За столом уже 12 игроков')
        name = clean(data.get('name'), 20)
        name_key = lambda v: ''.join(c for c in unicodedata.normalize('NFKC', v).lower().replace('ё', 'е') if c.isalnum())
        if not name_key(name):
            raise ValueError('Имя должно содержать буквы или цифры')
        if any(name_key(p['name']) == name_key(name) for p in r['players']):
            raise ValueError('Это имя уже занято')
        token = secrets.token_urlsafe(32)
        p = dict(id=secrets.token_hex(6), token=token, name=name, seen=self.clock(), points=0)
        r['players'].append(p)
        r['host'] = r['host'] or p['id']
        r['updated'] = self.clock()
        return dict(code=r['code'], token=token, id=p['id'])

    def auth(self, code, token):
        if not isinstance(token, str) or not token.isascii():
            raise ValueError('Некорректный сеанс')
        r = self.room(code)
        p = next((p for p in r['players'] if secrets.compare_digest(p['token'], token)), None)
        if not p:
            raise ValueError('Сеанс не найден. Войдите в комнату заново')
        p['seen'] = self.clock()
        r['updated'] = self.clock()
        self.tick(r)
        return r, p

    def online(self, p):
        return self.clock() - p['seen'] < PRESENCE

    def cleanup(self):
        for code in list(self.rooms):
            if self.clock() - self.rooms[code]['updated'] > ROOM_TTL:
                del self.rooms[code]

    def tick(self, r):
        now = self.clock()
        host = next((p for p in r['players'] if p['id'] == r['host']), None)
        available = [p for p in r['players'] if self.online(p)]
        if host and now - host['seen'] >= GRACE and available:
            r['host'] = available[0]['id']
        # Stale lobby seats cannot permanently fill a room.
        if r['stage'] == 'lobby':
            r['players'] = [p for p in r['players'] if now - p['seen'] < GRACE or p['id'] == r['host']]
        if r['stage'] in ('clues', 'guess'):
            guesser = next(p for p in r['players'] if p['id'] == r['guesser'])
            if now - guesser['seen'] >= GRACE:
                self.finish(r, '', 'Угадывающий отключился')
            elif now >= r['deadline']:
                if r['stage'] == 'clues':
                    self.filter_hints(r)
                else:
                    self.finish(r, '', 'Время вышло')

    def next_round(self, r):
        active = [p for p in r['players'] if self.online(p)]
        if r['number'] >= r['total']:
            r['stage'] = 'finished'
            r['deadline'] = None
            return
        if len(active) < MIN_PLAYERS:
            raise ValueError('Нужны хотя бы 3 игрока онлайн')
        previous = r['guesser']
        # Keep join order rotation even if an intermediate seat has disconnected.
        ids = [p['id'] for p in r['players']]
        start = (ids.index(previous) + 1) % len(ids) if previous in ids else 0
        candidates = ids[start:] + ids[:start]
        guesser = next(i for i in candidates if any(p['id'] == i for p in active))
        unused = [w for w in WORDS[r['mode']] if w not in r['used']]
        word = secrets.choice(unused or WORDS[r['mode']])
        r.update(stage='clues', number=r['number'] + 1, word=word, guesser=guesser,
                 participants=[p['id'] for p in active], hints={}, guess='', result=None,
                 deadline=self.clock() + HINT_TIME)
        r['used'].append(word)

    def filter_hints(self, r):
        counts = collections.Counter(normalize(h['text']) for h in r['hints'].values())
        for h in r['hints'].values():
            h['duplicate'] = counts[normalize(h['text'])] > 1
        r.update(stage='guess', deadline=self.clock() + GUESS_TIME)

    def finish(self, r, guess, reason=None):
        correct = bool(guess) and normalize(guess) == normalize(r['word'])
        if correct:
            r['score'] += 1
            next(p for p in r['players'] if p['id'] == r['guesser'])['points'] += 1
        r.update(stage='reveal', guess=guess, result=dict(correct=correct, reason=reason), deadline=None)
        r['history'].append(dict(number=r['number'], word=r['word'], correct=correct))

    def action(self, code, token, data):
        r, p = self.auth(code, token)
        action = data.get('action')
        if action in ('start', 'next', 'reset', 'remove') and p['id'] != r['host']:
            raise ValueError('Это действие доступно ведущему')
        if action == 'start' and r['stage'] == 'lobby':
            self.next_round(r)
        elif action == 'next' and r['stage'] == 'reveal':
            self.next_round(r)
        elif action == 'reset':
            r.update(stage='lobby', number=0, score=0, history=[], used=[], deadline=None,
                     word=None, guesser=None, hints={}, participants=[], result=None, guess='')
            for player in r['players']:
                player['points'] = 0
        elif action == 'remove' and r['stage'] == 'lobby':
            target = data.get('id')
            if target == r['host']:
                raise ValueError('Нельзя удалить ведущего')
            r['players'] = [player for player in r['players'] if player['id'] != target]
        elif action == 'hint' and r['stage'] == 'clues' and p['id'] in r['participants'] and p['id'] != r['guesser']:
            text = clean(data.get('text'), 40)
            if not re.fullmatch(r'[а-яёa-z]+(?:[-.!?,][а-яёa-z]*)*[.!?,]*', text, re.I) or len(normalize(text)) < 2:
                raise ValueError('Подсказка — одно слово из букв, без пробелов и цифр')
            if normalize(text) == normalize(r['word']):
                raise ValueError('Нельзя писать само загаданное слово')
            if p['id'] in r['hints']:
                raise ValueError('Подсказка уже принята')
            r['hints'][p['id']] = dict(text=text, duplicate=False)
            if len(r['hints']) == len(r['participants']) - 1:
                self.filter_hints(r)
        elif action in ('guess', 'pass') and r['stage'] == 'guess' and p['id'] == r['guesser']:
            self.finish(r, clean(data.get('text'), 60) if action == 'guess' else '', 'Пас' if action == 'pass' else None)
        else:
            raise ValueError('Сейчас это действие недоступно')
        return self.view(r, p)

    def view(self, r, p):
        # Explicit projection: never serialize engine objects or other players' credentials.
        reveal = r['stage'] in ('reveal', 'finished')
        hints = []
        for pid in r['participants']:
            if pid == r['guesser']:
                continue
            h = r['hints'].get(pid)
            visible = h and (reveal or (r['stage'] == 'guess' and not h['duplicate']) or (pid == p['id'] and r['stage'] == 'clues'))
            hints.append(dict(id=pid, submitted=bool(h), text=h['text'] if visible else None,
                              duplicate=h['duplicate'] if h and r['stage'] != 'clues' else False))
        return dict(code=r['code'], mode=r['mode'], total=r['total'], stage=r['stage'],
                    number=r['number'], score=r['score'], host=r['host'], me=p['id'], guesser=r['guesser'],
                    players=[dict(id=q['id'], name=q['name'], online=self.online(q), points=q['points']) for q in r['players']],
                    word=r['word'] if reveal or (p['id'] != r['guesser'] and r['stage'] in ('clues', 'guess')) else None,
                    hints=hints, guess=r['guess'], result=r['result'], history=r['history'],
                    seconds=max(0, int(r['deadline'] - self.clock())) if r['deadline'] else None,
                    bank_size=len(WORDS[r['mode']]))
