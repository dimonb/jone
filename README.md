# Jone — одна подсказка

Original Russian-language cooperative word-association web game for 3–12 players.
Easy / hard decks plus a separate adult deck requiring explicit 18+ confirmation
from **every** joining player. Curated word lists are authored here, not extracted
from commercial cards; no third-party artwork, fonts, trackers or runtime packages.

## Local run and test

Python 3.11+ (container uses 3.14.10), browser with modern DOM/fetch support. No pip/npm
installation is necessary at runtime: engine/HTTP tests use the stdlib.
Browser regressions use the pinned Playwright dev dependency (`npm ci`,
`npx playwright install chromium`, `npm run test:browser`).

```sh
cd jone
python3 -m unittest discover -s tests -v
node --check static/app.js
PORT=8080 python3 server.py
# open http://localhost:8080 in separate browser contexts for each player
```

Container (dedicated image, never the devbox/beta image):

```sh
docker build -t jone:local .
docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges \
  -p 8080:8080 jone:local
```

Runs as numeric UID/GID 10001. Port `8080`; `GET /healthz` is a dependency-free
readiness/liveness endpoint (`APP_VERSION`, optional, defaults to `local`).
Do not expose this Python server directly to the internet: terminate TLS and
bound body/read/request timeouts at the ingress. The service limits HTTP workers
(64), body size (8 KiB), rooms (200), seats (12), and peer request rates.

## Rules and state

Host starts with at least 3 online players. Guesser rotates in seat order, skipping
offline seats; the host role transfers after 45 seconds disconnected. Helpers
have 120 seconds to submit one immutable clue. Duplicate clues normalize Unicode,
case, ё→е and punctuation; all members of a duplicate group disappear. Guesser
gets one attempt (90 seconds), or can pass. Correct answer adds one team point;
individual correct answers are recorded too. Deck draws avoid repeats per party.
A round ends when the guesser disconnects for 45 seconds. Host can reset to lobby
or remove lobby seats. Translation/root-word rule is social, not a morphological
filter: the literal normalized target itself is rejected server-side.

The browser polls every 2 seconds. Invitations are `/room/CODE` URLs containing
only a public room code, never a player token. Legacy `/#CODE` links still work.
Identity is saved **per room in localStorage**, with sessionStorage fallback;
refresh, new tabs and returning to a different known room resume the same seat.
An explicit invitation to another room takes precedence over an old session.
Separate browser profiles/private contexts are needed for different players.
Storage-blocking webviews still work in-memory but cannot promise recovery after
closing the tab; the UI warns when persistent storage cannot be written.
Browser storage can be cleared by the user/OS, and is accessible to same-origin
scripts (the app's restrictive CSP remains in place). Do not share a browser
profile with a competing player. Leave forgets that room's saved identity and
stops polling; its seat becomes offline then is pruned in the lobby after the
grace period. Removed/expired credentials return to an explicit join form; network
failures preserve credentials and retry GET only, never automatically add a seat.
Existing idle-lobby pruning still removes non-host seats after 45 seconds away;
server restart/room expiry cannot be repaired by a saved browser token.

All room state is in one process's RAM, with a lock around engine access. **Deploy
exactly one replica**; use a **Recreate** strategy (no rolling overlapping processes).
No PVC or database. Restart/deploy intentionally loses ongoing rooms; idle rooms
expire after 6 hours. This must be disclosed to players (also on the welcome page).
Do not log Authorization headers at the ingress. Application request logging is
disabled to avoid retaining words, tokens, nicknames or room codes.

The server serializes explicit per-player views: the current target is absent
from the guesser's API responses until reveal; other helpers' pre-submission clues
and duplicate clues are hidden. This protects against accidental API/DOM spoilers,
not colluding players sharing their tokens/screens. UI renders user strings via
textContent, never HTML. Same-origin POST checks, CSP, no-store, frame denial and
nosniff headers are enforced. Room codes are invite identifiers, not passwords;
anyone with the code can request a seat while in the lobby. Adult confirmation
is a self-declaration, not identity verification.

## Image delivery

`.github/workflows/jone.yaml` runs offline engine/HTTP tests and JavaScript syntax
checks. Main builds `ghcr.io/dimonb/jone:<commit>`, runs a read-only non-root
container health smoke, and publishes an immutable digest. A package can default
private: anonymous pull must be verified separately before deployment.

Deployment configuration is maintained separately, not in this public repository.
Publishing an image does not activate a deployment. Activation requires independent
review of the exact digest, one replica/Recreate, TLS ingress and runtime checks.
No cluster configuration, credentials or infrastructure history is included here.
