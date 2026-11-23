# NULL CITY private application API

Same-origin HTTPS only. Authentication uses an opaque `__Host-nullcity` cookie
(`Secure; HttpOnly; SameSite=Strict`). Mutations require exact `Origin`, the
`X-NullCity: 1` header and `Content-Type: application/json`. No CORS permissions
are emitted. Body limit is 200,000 bytes. Passwords: 12–128 characters. Public
API/API-key module is **not** claimed; these are authenticated application routes.

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/health` | DB connectivity; no sensitive status fields |
| POST | `/api/register`, `/api/login` | email/password; register also requires name; login returns a 2FA challenge instead of a session when the account has 2FA ([details](#email-two-factor-authentication-2fa)) |
| POST | `/api/login/2fa` | complete a 2FA login with the emailed or a recovery code |
| POST | `/api/login/2fa/resend` | send a new login code |
| POST | `/api/2fa/setup` | start enabling 2FA (password, emails a code) |
| POST | `/api/2fa/enable` | finish enabling 2FA; returns 8 recovery codes |
| POST | `/api/2fa/disable` | two-step disable (password, then code) |
| POST | `/api/logout` | invalidate current session |
| GET/PUT/DELETE | `/api/me` | own profile (includes `email`, `twofa`); update name/bio; DELETE erases the account (password required) |
| PUT | `/api/avatar` | base64 PNG/JPEG or `image: null`; decode/re-encode |
| GET | `/api/avatar/:id` | authenticated base64 PNG preview |
| GET/PUT | `/api/save` | own campaign JSON; PUT requires expected revision |
| GET | `/api/users?q=&online=1&sort=name&page=0` | filtered/sorted/paged public profiles |
| GET/POST/DELETE | `/api/friends` | list, invite/accept, remove; mutation `userId` |
| GET/POST | `/api/messages` | latest 60 public channel messages; POST `body` |
| GET | `/api/events` | SSE invalidation sequence; reconnect after 25-second stream |
| GET/POST | `/api/rooms` | room list/create; name, capacity, topic, duration |
| GET | `/api/rooms/:id` | authorized user may observe; snapshot, no answer key |
| POST | `/api/rooms/:id/join` | join only while waiting and below capacity |
| POST | `/api/rooms/:id/start` | host only, 2+ participants |
| POST | `/api/rooms/:id/answer` | round and choice, once per participant/round |
| POST | `/api/rooms/:id/hint` | round; once per participant/match, excludes a wrong option |
| GET | `/api/stats` | own history and aggregate stats; public leaderboard |
| GET | `/api/export` | own non-secret profile/data export; optional `?lang=ru\|en\|es`; 10 per hour per account (429); sends a confirmation mail |

Save writes use conditional revisions. An outdated writer gets HTTP 409, and must
load/reconcile the newer state. The server does not accept client-provided arena
scores; only the answer key increments scores. All game transitions run inside an
IMMEDIATE SQLite transaction. Disconnected players can query the same room and
continue; missed deadlines score zero. No wall-clock speed bonus penalizes latency.
Question answers/explanations are returned only after a finished match.

Registration/login are limited to 10 attempts per 5 minutes per IP; API traffic to
300 requests/minute per IP; chat to 20 messages/minute per account; room creation
to 5 per 5 minutes. Limits are in-memory and reset on restart. This is deliberately
a single-process implementation, not a distributed rate limiter or production
DDoS protection system. Scrypt hashing has a bounded concurrency semaphore.
The additional 2FA limits are listed in the 2FA section below.

All errors are JSON `{"error": "<message>"}` (messages are in Russian) plus,
where noted, extra fields such as `retryAfter`.

## Email two-factor authentication (2FA)

Opt-in, per account. Codes are 6 digits, valid 10 minutes (`expiresIn: 600`), at most
5 attempts per challenge, single use. Eight single-use recovery codes are issued when 2FA is
enabled. Operation, limits and the full call chain: [SMTP-2FA.md](SMTP-2FA.md).

A **challenge** is an opaque string (`challenge`) bound to one account and one purpose
(`login`, `enable` or `disable`); a challenge of another purpose or account is answered
with `410`. The optional body field `lang` (`ru`, `en`, `es`; anything else becomes `en`)
selects the language of the email.

Common challenge response (`setup`, `disable` step 1, and the body of a 2FA `login`):

| Field | Type | Present | Description |
|---|---|---|---|
| `challenge` | string | always | opaque token, send it back with the code |
| `expiresIn` | integer | always | seconds, `600` |
| `emailHint` | string | always | masked address, e.g. `a***@example.com` |
| `mailFailed` | boolean | only when no email was sent | the code was **not** delivered; only a recovery code can complete this challenge (or a resend, for login) |
| `mailLimited` | boolean | with `mailFailed` | the account's hourly mail budget (6 per hour) is exhausted; no `retryAfter` |
| `retryAfter` | integer | with `mailFailed`, unless `mailLimited` | seconds until another email may be requested (at most 30) |

### POST /api/login

**Auth**: none. Body: `email`, `password`, optional `lang`.

Account **without** 2FA: unchanged (`200`, profile, `Set-Cookie`; the profile contains `"twofa": false`).

Account **with** 2FA: `200`, **no cookie**, and an email is sent:

```json
{"twofa": true, "challenge": "q3Zr...", "expiresIn": 600, "emailHint": "a***@example.com"}
```

If the email could not be sent (SMTP down, 30 s cooldown, hourly budget), the same `200`
is returned with `mailFailed` and either `retryAfter` or `mailLimited`:

```json
{"twofa": true, "challenge": "q3Zr...", "expiresIn": 600, "emailHint": "a***@example.com", "mailFailed": true, "retryAfter": 30}
```

Errors: `400` invalid email/password length, `401` wrong email or password, `429` rate limit.

### POST /api/login/2fa

Complete a 2FA login. **Auth**: none (the challenge is the proof of the first factor).

| Field | Type | Required | Description |
|---|---|---|---|
| challenge | string | yes | from `POST /api/login` (ASCII, at most 100 characters) |
| code | string | yes | the 6-digit emailed code, **or** a recovery code (`xxxxx-xxxxx`; case, spaces and hyphens are ignored); ASCII, at most 64 characters |

**Response** (200): the private profile and `Set-Cookie: __Host-nullcity=...`.

```json
{"id": 7, "name": "Alice", "bio": "", "created": 1763900000, "online": true, "avatar": null, "email": "alice@test.local", "twofa": true}
```

**Errors**:
- 400: `challenge` or `code` missing, not a string, not ASCII, or too long (costs no attempt)
- 403: wrong code (costs one of the 5 attempts; a wrong 6-digit code also counts against the account budget)
- 410: challenge unknown, expired, already used, wrong purpose, or 5 attempts spent
- 429: rate limit (20 per 5 minutes per IP), or 10 wrong 6-digit codes on the account in 15 minutes; during that lock **6-digit codes are refused even when correct, recovery codes still work**

### POST /api/login/2fa/resend

Send a new code for a pending **login** challenge. **Auth**: none.

Body: `challenge` (required), optional `lang`.

**Response** (200): `{"ok": true, "expiresIn": 600}`. The new code replaces the previous one only after the
mail was accepted by the SMTP server; attempts are reset to 0 and the expiry is pushed back 10 minutes.

**Errors**:
- 400: `challenge` missing or malformed
- 410: challenge unknown, expired, used, or not a login challenge
- 429: 30 s cooldown (`retryAfter` present), hourly budget exhausted (no `retryAfter`), or IP rate limit
- 503: the email could not be sent; `retryAfter: 30`. The previous code stays valid.

### POST /api/2fa/setup

Start enabling 2FA for the signed-in account and email a code (purpose `enable`).

**Auth**: Required (session cookie).

| Field | Type | Required | Description |
|---|---|---|---|
| password | string | yes | the account password, 12 to 128 characters |
| lang | string | no | `ru`, `en` or `es` (default `en`) |

**Response** (200): `{"challenge": "...", "expiresIn": 600, "emailHint": "a***@example.com"}`

**Errors**:
- 400: password missing or not 12 to 128 characters
- 401: not signed in
- 403: wrong password (does **not** end the session)
- 409: 2FA is already enabled
- 429: more than 5 password attempts in 5 minutes, 30 s mail cooldown (`retryAfter`), or hourly mail budget (no `retryAfter`)
- 503: email could not be sent or SMTP is not configured; `retryAfter: 30`. 2FA stays disabled.

### POST /api/2fa/enable

Verify the emailed code, turn 2FA on and return the recovery codes.

**Auth**: Required.

| Field | Type | Required | Description |
|---|---|---|---|
| challenge | string | yes | from `setup` |
| code | string | yes | the 6-digit emailed code |

**Response** (200):

```json
{"ok": true, "recoveryCodes": ["k3m9p-2xq7a", "...", "(8 codes in total)"]}
```

The codes are shown **only in this response**; the server keeps only keyed digests. As part of the same
transaction all **other** sessions of the account are revoked (the calling session stays valid) and any
older recovery codes and pending challenges are discarded.

**Errors**:
- 400: `challenge`/`code` missing, not strings, not ASCII, or too long
- 401: not signed in
- 403: wrong code
- 409: 2FA is already enabled
- 410: challenge dead (expired, used, wrong purpose or account, 5 attempts spent)
- 429: rate limit (20 per 5 minutes per account) or 10 wrong 6-digit codes in 15 minutes

If the response is lost after a successful call, 2FA is on and the codes cannot be shown again; disable and re-enable.

### POST /api/2fa/disable

Two steps on the same route; both require the password. Step 2 is recognised by the presence of the `challenge` key.

**Auth**: Required.

**Step 1** body: `password`, optional `lang`. Response (200) is the common challenge response above
(purpose `disable`). This step **never** fails because of mail: if no email can be sent, the challenge is
still returned with `mailFailed` so that a recovery code can complete step 2.

**Step 2** body:

| Field | Type | Required | Description |
|---|---|---|---|
| password | string | yes | the account password |
| challenge | string | yes | from step 1 |
| code | string | yes | the emailed 6-digit code or a recovery code |

**Response** (200): `{"ok": true}`. 2FA is off; recovery codes and challenges are deleted. Sessions are not revoked.

**Errors**:
- 400: password not 12 to 128 characters, or `challenge`/`code` malformed
- 401: not signed in
- 403: wrong password or wrong code
- 409: 2FA is not enabled
- 410: challenge dead
- 429: rate limit; step 1 allows 5 password checks per 5 minutes and account, step 2 allows 10; or 10 wrong 6-digit codes in 15 minutes

### Other 2FA-related routes

- `GET /api/me` and `GET /api/export`: the profile contains `email` and `twofa` (boolean). No challenge,
  code or recovery secret is ever returned or exported.
- `DELETE /api/me` (account erasure): body `{"password": "...", "lang": "en"}` (`lang` optional); a confirmation mail is sent in the background after the commit, and a mail outage never blocks export or erasure; `200` with `{"ok": true, "message": "..."}` and an expired cookie;
  `403` on a wrong password, `400` on a malformed one, `429` after 5 attempts in 5 minutes per account. It requires a
  valid session and the password only: **no 2FA code**. All 2FA rows are deleted by cascade.
- `POST /api/2fa/<anything else>`: `404`.

### 2FA limits at a glance

| Limit | Value |
|---|---|
| Challenge lifetime | 600 s |
| Attempts per challenge | 5 (6-digit and recovery codes both count) |
| Wrong 6-digit codes per account | 10 per 900 s (recovery codes are exempt) |
| Emails per account | 1 per 30 s and 6 per 3600 s |
| Email-sending requests per IP | 10 per 300 s |
| `login/2fa` and `login/2fa/resend` per IP | 20 per 300 s |
| `/api/2fa/*` per account | 20 per 300 s; password checks 5 per 300 s (setup, disable step 1) and 10 per 300 s (disable step 2) |

Rate limiter responses (`429`) have no `retryAfter`; the in-memory limiter resets on restart. Per-account mail and
wrong-code budgets are stored in the database and survive restarts.

## Lobby and progression (1.0)

All endpoints below require an authenticated session and the existing same-origin write checks.

- `POST /api/matchmaking {}`: atomically return the caller's active room, join an available recent mixed/30s lobby, or create a four-seat lobby.
- `POST /api/rooms/:id/ready {"ready": true}`: toggle the caller's readiness while waiting. Spectators cannot mark ready.
- `POST /api/rooms/:id/start {}`: host only; at least two participants, every participant ready.
- `POST /api/rooms/:id/leave {}`: waiting lobbies only. Transfers host to a remaining participant; empty lobbies become cancelled. During a live match use the lobby list to reconnect; the server clock continues.
- `POST /api/rooms/:id/rematch {}`: participants in a finished match can opt into one shared successor lobby. Concurrent requests cannot create separate successors. Every player must consent and ready again; nobody is enrolled automatically.
- `GET /api/stats`: includes `rank`, `nextLevelXp`, and `daily` (`progress`, `target`, `reward`, `complete`, `resetsAt`). Three correct answers across finished matches in a UTC day add 40 XP once. These totals are derived from persisted match results, never from client claims; requesting stats again cannot grant another reward.

Room snapshots include `players[].ready` and `rematch_of`. Invite links identify public rooms and grant no private access. The question bank contains 28 original teaching scenarios. The main menu uses the same register/login/logout endpoints as the multiplayer client. Offline HTML does not emulate an account server.
