*This project has been created as part of the 42 curriculum by [actual team logins pending].*

# NULL CITY — SIGNAL LOST (1.0)

> Team attribution is not complete. Replace the bracketed text with the real 4–5
> student logins and fill the team sections before submission. No team members,
> commits, meetings or human contributions have been invented.

## Description

NULL CITY is a Russian, English and Spanish cyberpunk learning RPG and multiplayer knowledge
club. The first story follows an operator investigating a resident erased from a
city registry. Eleven cases across two chapters introduce files, routing, DNS, evidence, containment,
reporting, phishing checks, least privilege, backup recovery and responsible disclosure.
Five walkable interiors connect optional stories, a radio station, a memory archive
and an XP clothing shop. Chapter two has two persistent endings, 850 additional XP
and visible changes to the city after recovery and broadcast. Existing saves continue
from case eight without resetting appearance or purchases.
The capital map connects four districts with progression gates, three new terminal operations and a final confrontation with REGENT. District progress and one-time XP rewards survive reloads; see [capital notes](docs/CAPITAL.md).
The identity editor has original 32×48 source sprites displayed at the original 16×24 world scale, seven hairstyles, custom
skin/hair/eye colours, gender selection and directional animated previews.

The main menu includes Multiplayer, Sign in / Register, and a campaign dossier with six achievement milestones. The multiplayer lobby supports atomic quick match, public invitation links, ready checks, host transfer and shared opt-in rematches. A daily learning contract rewards three correct answers in completed matches once per UTC day. See [release notes](docs/RELEASE-1.0.md).

The HTTPS club adds email/password accounts, editable profiles, raster avatars,
friends, a live public chat and a server-authoritative 2–4-player quiz arena. It
has spectators, one-use hints, configurable topics/timers, match history, badges,
XP/levels and a leaderboard. OSCP/Security+ routes currently contain an outline
and one introductory activity each; they are not complete certification courses.

The city uses native Canvas architecture with 50 facade identities, denser blocks and a walkable ring-shaped capital. Obsolete image atlases have been removed.

The SIGNAL LOST art pass adds a separate illustrated VHS bedroom menu, six cassette slots
with honest availability labels, blue-grey rain-soaked streets, warm interiors, four story
portraits, a paper journal and a readable CRT terminal. Seven weather/time presets (day, sunset, night, rain, downpour, fog, storm), a smooth cycle,
animated water reflections and reusable rooftop sprite modules are integrated into the renderer.
Three quality modes and independent
rain, grain, scanline, reduced-motion and volume controls are saved with the game.
See [the art implementation notes](docs/SIGNAL-LOST.md) for scope and asset provenance.

## Instructions

### Full application (evaluation target)

Prerequisites: Docker Engine with Compose v2; a modern browser; Internet access for
the initial image build. Optional Node.js 20+ runs the frontend tests/build. Native
server tests require Python 3.12+, Pillow and OpenSSL.

```sh
cp .env.example .env
docker compose up --build -d
```

Open **https://localhost:8443/**; use **Multiplayer** or **Sign in / Register** from the main menu.
The server automatically generates a self-signed TLS certificate with SAN entries
covering `localhost`, `127.0.0.1`, and detected local LAN network interfaces.

```sh
docker compose ps
docker compose logs --tail=30
docker compose cp nullcity:/app/data/tls/cert.pem ./nullcity-local-cert.pem
```

#### Multiplayer across multiple devices in the local network (LAN)

To play multiplayer from different devices (phones, laptops, tablets) in the same Wi-Fi/LAN:
1. Ensure `BIND=0.0.0.0` in `.env` (default).
2. Set `HOST_IP=<YOUR_LAN_IP>` (e.g. `192.168.1.50`) in `.env` so the TLS certificate SAN and CORS/CSRF origin checks match your machine.
3. Open `https://<YOUR_LAN_IP>:8443/hub.html` from any other device on the same network.
4. When prompted by the browser about the self-signed certificate, accept the security exception (or import `cert.pem` into device trust store).
5. Players can create rooms, copy the invitation link, mark ready, and play live server-authoritative matches together.
See [SUBMISSION_GUIDE.md](SUBMISSION_GUIDE.md) for detailed evaluation walkthroughs.

The named `city-data` volume persists accounts, avatars, saves and match data.
`docker compose down` stops the stack without deleting that volume.

### Native HTTPS fallback

If the host cannot execute containers, a native fallback is available:

```sh
python3 scripts/serve.py
```

It needs Python 3.12+, Pillow and OpenSSL, reads the allowed keys from `.env`,
generates a private local TLS key if needed and serves the same API at
`https://localhost:8443`. This fallback does **not** satisfy the mandatory proof
of working container deployment. The current host (rechecked 21 October 2026) builds the Docker image but
rejects runtime exec with `operation not permitted`; resolve that host-level
problem and repeat Compose validation before evaluation.

### Offline single-player edition

Run `npm run dev` and open https://localhost:8443/ for the game with optional accounts. A trusted local TLS certificate is required. `npm run preview:static` serves a static preview at http://127.0.0.1:4173/; it cannot register or sign in. The generated `NULL-CITY.html` supports single-player without an account.
`npm run build` rebuilds the single-file offline story. The club requires the HTTPS
server and is not embedded in the standalone HTML. Privacy/Terms and club links
in the standalone edition refer to sibling files; distribute the project folder
for those pages. No account is needed for the offline story.

### Controls

WASD/arrows move; Shift runs; E interacts; G routes to the current case; V toggles
network vision; M toggles the map; I opens inventory; J opens the journal; Escape closes dialogs. Click a street to walk.
Use Places to route to doors. Character opens the free identity editor; clothing
is purchased and equipped inside NEON THREAD. Touch movement controls are included.

### Tests and backup

```sh
npm run check
npm test
npm run test:server
npm run build
```

The server tests create an isolated temporary database and trusted temporary TLS
certificate, then use actual HTTPS requests; they do not touch the running data.
They cover auth/CSRF, simultaneous save writes, user isolation, friends/chat,
four-client matches, duplicate answers, hints, reconnection state and spectators.

```sh
docker compose exec nullcity python3 server/backup.py /app/data/nullcity.sqlite3 /app/data/backup.sqlite3
docker compose cp nullcity:/app/data/backup.sqlite3 ./backup.sqlite3
```

Use a new destination for each backup. The SQLite backup API captures WAL safely
and checks integrity. For restore, stop the application, retain a copy of current
data, replace the DB with a verified backup and remove obsolete WAL/SHM sidecars
while stopped. Restart and check accounts/saves. The server also takes verified automatic snapshots at startup and every six hours,
retaining seven copies by default. The public status page reports database and
backup health. See [recovery and drill instructions](docs/RECOVERY.md) for the
non-destructive restore command, failure behavior and off-site storage limits.

## Languages and service status

Choose Русский, English or Español on the cover, in menus or during play. The
choice survives reloads without resetting campaign progress. Story, tutorials,
terminal explanations, arena questions, errors and policy pages use the local
catalog. Commands and user-authored text remain unchanged. See
[localization maintenance](docs/LOCALIZATION.md).

Open `/status.html` on the HTTPS deployment to inspect database integrity and the
last verified automatic backup. `BACKUP_INTERVAL_SECONDS` and `BACKUP_RETAIN`
configure the schedule and retention. [Recovery documentation](docs/RECOVERY.md)
describes testing and restoring a snapshot.

## Team Information

| Real login | Role | Responsibility | Status |
|---|---|---|---|
| Pending | Product Owner | Priorities, acceptance and scope | Assign a real member |
| Pending | Project Manager | Work distribution, coordination and risks | Assign a real member |
| Pending | Technical Lead | Architecture, security and code review | Assign a real member |
| Pending | Developers (all members) | Implementation, tests and peer review | Record each member |

## Project Management

The repository contains an evaluation matrix (`docs/EVALUATION.md`), roadmap and
verification log. The actual team's issue tracker, meeting cadence and communication
channel have not been provided. Record real decisions and feature ownership here;
these files do not substitute for collaboration or meaningful Git history.

## Technical Stack

- Frontend: native ES modules, Canvas 2D, accessible HTML forms, custom CSS styling
  solution. No frontend framework points are claimed.
- Backend: Python standard-library threaded HTTPS server. A small, explicit API
  makes state transitions inspectable; no backend framework points are claimed.
- Database: SQLite with WAL, foreign keys, parameterized statements, per-request
  connections and transactions. Well suited to the single-process learning deployment;
  not a distributed service. No ORM points are claimed.
- Pillow: decode, resize and re-encode PNG/JPEG avatars to strip metadata and reject
  invalid payloads; SVG and arbitrary uploads are not accepted.
- Server-Sent Events notify clients of chat/lobby/social changes; arena snapshots
  poll once per second to advance deadlines and recover after disconnections.
- Docker Compose: unprivileged, read-only application filesystem, writable named
  data volume, TLS-only published endpoint and a verified-certificate health check.

## Database Schema

See `server/schema.sql`. Every relationship below is enforced by a foreign key.

```mermaid
erDiagram
  users ||--o{ sessions : authenticates
  users ||--o| saves : owns
  users ||--o{ friends : sends_or_receives
  users ||--o{ messages : writes
  users ||--o{ rooms : hosts
  users ||--o{ players : participates
  rooms ||--o{ players : contains
  players ||--o{ answers : submits
```

`users`: integer ID, unique email, salted password hash, display name/bio,
validated avatar BLOB, creation/last-seen timestamps. `sessions`: SHA-256 token
hash, user FK, expiry. `saves`: unique user FK, JSON payload, optimistic revision,
update time. `friends`: sender/recipient composite key and acceptance flag.
`messages`: author FK, validated body, timestamp. `rooms`: host FK, settings,
private deck, round, deadline and state. `players`: room/user composite key, server
score and one-use hint state. `answers`: unique room/user/round and accepted choice.

## Features List

| Feature | Files | Human ownership |
|---|---|---|
| City, pixel assets, movement, interiors | `src/engine.js`, `src/art.js`, `src/places.js` | To be recorded |
| Identity editor, layered original sprites | `src/characters.js`, `src/app.js` | To be recorded |
| Story and stateful incident labs | `src/campaign.js`, `src/simulator.mjs` | To be recorded |
| Accounts, storage, social features, arena | `server/app.py`, `server/schema.sql` | To be recorded |
| Club UI and results | `hub.html`, `src/hub.js`, `src/hub.css` | To be recorded |
| TLS/container startup and backup | `compose.yaml`, `deploy/`, `server/backup.py` | To be recorded |
| Tests and documentation | `tests/`, `docs/` | To be recorded |

## Modules

The candidate route totals **19 points (14 + 5 bonus)**. This is not a validated
grade. Detailed requirements, evidence, dependencies and outstanding demonstrations
are in `docs/EVALUATION.md`.

| Candidate module | Points |
|---|---:|
| Real-time features | 2 |
| User interaction (chat/profiles/friends) | 2 |
| Standard user management | 2 |
| Complete competitive web game | 2 |
| Remote players | 2 |
| Multiplayer 3+ | 2 |
| Three complete UI/content languages and switcher | 1 |
| Health/status, automatic backups and recovery procedures | 1 |
| Game statistics/history | 1 |
| Game customization | 1 |
| Gamification | 1 |
| Spectator mode | 1 |
| Advanced search | 1 |
| **Candidate total** | **19** |

Each feature's implementation is described above and in the evaluation matrix.
Team ownership remains pending. The 19-point route now uses listed modules; the
stateful simulator is additional functionality and is not needed as a discretionary
custom major in this calculation. Remote play, current Chrome and complete UI flows
must be demonstrated; partial modules must not be counted.

## Individual Contributions

Not supplied. Each real team member must document their own features, commits,
reviews, tests, challenges and understanding before submission. AI-assisted output
must not be presented as an invented human contribution.

## Resources

- Subject: user-supplied `567.pdf`, ft_transcendence v21.2. This implementation was
  mapped against its mandatory requirements and module descriptions.
- [MDN Canvas](https://developer.mozilla.org/en-US/docs/Web/API/Canvas_API)
- [MDN Server-Sent Events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events)
- [Python sqlite3](https://docs.python.org/3/library/sqlite3.html)
- [SQLite transactions](https://www.sqlite.org/lang_transaction.html)
- [Docker Compose](https://docs.docker.com/compose/)
- [OWASP password storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)
- [OWASP CSRF prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)
- [OffSec PEN-200](https://www.offsec.com/courses/pen-200/)
- [CompTIA Security+](https://www.comptia.org/en-us/certifications/security/)

AI assistance: Codex helped analyse the subject, plan the architecture, author
pixel-rendering code, UI, simulator features, backend, tests and documentation.
The team must review, understand, adapt and take responsibility for all submitted
code. Peer review and the ability to modify/explain code remain essential.
No Terraria assets are copied; all artwork and character sprites are original assets.

## Known limitations

This is an evaluation-preparation build, not a production security service. The
server is single-process; SSE has one global invalidation channel. There is no
email verification, recovery, 2FA, moderation UI, automatic account deletion,
WAF/Vault, ORM or certification-complete curriculum. The question bank is small
and educational, not an exam dump. Offline campaign saves are user-controlled and
never determine the competitive arena leaderboard. Remote device verification,
team attribution/history and an independent security/accessibility review are
outstanding. See the dated verification log for exact checks performed.
