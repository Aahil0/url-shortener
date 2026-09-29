# Shortly: URL shortener with click analytics

A small full-stack app: a Python (FastAPI) REST API, a SQLite database, and a single-page frontend.
Shorten a link, share it, and see clicks per day, top sources, devices, and unique visitors, without ever storing a visitor's IP address.

**Live demo:** http://3.106.204.246/ &nbsp;|&nbsp; **API docs:** `/docs` (interactive, auto-generated)

## Features
- Random 7-character base62 short codes, or your own custom alias
- Optional expiry (1-365 days); expired links return `410 Gone`
- Click analytics: 30-day chart, referrer sources, device and browser breakdown, unique visitors
- Bot and link-preview traffic (Slack, WhatsApp, curl...) is counted separately, not as real clicks
- Input validation, reserved-word protection, per-IP rate limiting on link creation
- Dockerised, with automated tests and CI (GitHub Actions)

## API
| Method | Path | Description |
|---|---|---|
| `POST` | `/api/links` | Create a link. Body: `{"url", "custom_alias"?, "expires_in_days"?}`. Returns `201`, `409` (alias taken), `422` (invalid), `429` (rate limited) |
| `GET` | `/{code}` | Redirect (`302`) and record the click. `404` unknown, `410` expired |
| `GET` | `/api/links/{code}` | Link details |
| `GET` | `/api/links/{code}/stats` | Analytics for the last 30 days |
| `GET` | `/health` | Liveness check (also used by the Docker healthcheck) |

```bash
curl -X POST localhost:8000/api/links -H 'content-type: application/json' \
     -d '{"url": "https://example.com/a/very/long/path", "custom_alias": "demo"}'
```

## Run it
**Locally** (Linux, macOS, or WSL):
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest                                                    # run the tests
uvicorn app.main:create_app --factory --reload            # http://localhost:8000
```
**With Docker:**
```bash
PORT=8000 docker compose up --build                       # http://localhost:8000
```

## Deploy to AWS EC2 (Free plan)
1. Launch an **Ubuntu 24.04** instance, type `t3.micro`. In its security group allow **SSH (22) from your IP only** and **HTTP (80) from anywhere**.
2. SSH in and install Docker: `curl -fsSL https://get.docker.com | sudo sh`
3. `git clone <your-repo-url> && cd <repo>`, then `cp .env.example .env` and set `BASE_URL=http://<public-ip>` and `VISITOR_SALT=$(openssl rand -hex 16)`.
4. `sudo docker compose up -d --build`, then open `http://<public-ip>`.

Notes: the public IP changes if you stop and start the instance unless you attach an Elastic IP (then update `BASE_URL`). This setup serves plain HTTP; for HTTPS, point a domain at the server and put Caddy or nginx in front (set `TRUST_PROXY=true` only when you do).

## Project layout
```
app/main.py       routes (HTTP layer only)
app/services.py   validation, code generation, click recording, analytics queries
app/db.py         SQLite schema and connections
app/schemas.py    request/response models (Pydantic)
app/ratelimit.py  sliding-window rate limiter
app/config.py     settings from environment variables
app/static/       the single-page frontend (no build step, no dependencies)
tests/            19 tests: API behaviour, validation, expiry, analytics, rate limiting
```

## Design decisions and trade-offs
**FastAPI.** Request validation and OpenAPI docs come from the type hints, so the API is self-documenting at `/docs`. Flask would need extra libraries for both.

**SQLite, not Postgres.** Zero setup, one file, and plenty for a single small server. WAL mode lets reads proceed while a click is being written. The cost is a single writer and a single machine: to run several app instances I'd move to Postgres. The SQL is standard and isolated in `db.py` and `services.py`, so that swap is small.

**Random short codes, not sequential IDs or URL hashes.**
- Sequential IDs encoded in base62 are shorter but enumerable: anyone can walk the codes and see how many links exist.
- Hashing the URL gives the same code for the same URL, but collisions are harder to handle and every link would share settings.
- Random 7-character base62 gives about 3.5 trillion codes, is unguessable, and collisions are caught by the `UNIQUE` constraint and retried. The price is one extra DB attempt on the rare collision.

**`302`, not `301`.** Browsers cache `301` redirects, so repeat clicks would never reach the server and analytics would undercount. `302` plus `Cache-Control: no-store` guarantees every click is seen, at the cost of slightly more traffic.

**Clicks are recorded after the response.** A background task writes the click so analytics never slow down the redirect. Trade-off: if the process crashes between the response and the write, that click is lost. At larger scale I'd push events onto a queue.

**Privacy-friendly visitor counting.** Instead of storing IPs, each click stores `sha256(secret_salt + date + IP + user_agent)`, truncated. That still lets me count distinct visitors, but the IP can't be recovered. Trade-off: the hash rotates daily, so a visitor who returns on another day counts again, and "unique visitors" is an approximation. Set `VISITOR_SALT` in production so it survives restarts.

**Bot filtering by user agent.** Link previews and crawlers would otherwise inflate click counts. The rules are simple substring matches: easy to explain, but crude and spoofable. Bot clicks are stored and reported separately rather than dropped.

**Validation and abuse controls.**
- Only `http` and `https` are allowed (blocks `javascript:` and `data:` links).
- Length limits, and links pointing back at this service are rejected to prevent redirect loops.
- Reserved aliases (`api`, `docs`, ...) can't shadow real routes.
- Not covered: checking destinations against phishing or malware lists, which a public shortener would need.

**In-memory rate limiting.** 30 link creations per IP per minute. It is per-process, resets on restart, and can't be shared between instances; for multiple servers I'd use Redis or an API gateway. `X-Forwarded-For` is only trusted when `TRUST_PROXY=true`, because clients can forge it.

## Live Screenshots:
<img width="946" height="902" alt="image" src="https://github.com/user-attachments/assets/35621877-78d2-457a-a0be-a65e3cdcb7b9" />
<img width="886" height="572" alt="image" src="https://github.com/user-attachments/assets/460e1562-6aef-4132-a161-8e32e7347e70" />



**No accounts.** Anyone who knows a code can view its stats, and the frontend remembers your links in `localStorage`. Random codes are unguessable, but custom aliases are not, so a real product needs ownership, authentication, and deletion. Codes and aliases are case-sensitive, which increases the code space but can confuse people who retype them.

## Possible next steps
Postgres, a Redis cache for the redirect hot path, user accounts with link management, QR codes, and infrastructure as code (Terraform) for the AWS setup.
