"""Business logic: validation, code generation, click recording, statistics."""
import hashlib
import re
import secrets
import sqlite3
import string
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from . import db

ALPHABET = string.ascii_letters + string.digits      # base62
CODE_LENGTH = 7                                      # 62**7 ~ 3.5 trillion codes
ALIAS_RE = re.compile(r"[A-Za-z0-9_-]{3,32}")
RESERVED = {"api", "docs", "redoc", "openapi.json", "health", "static", "favicon.ico"}
MAX_URL_LENGTH = 2048


class AliasTaken(Exception):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------- validation ----------
def validate_url(raw: str) -> str:
    url = raw.strip()
    if not url or len(url) > MAX_URL_LENGTH or re.search(r"\s", url):
        raise ValueError(f"Enter a link without spaces, up to {MAX_URL_LENGTH} characters")
    parts = urlparse(url)
    # Allow-list schemes: blocks javascript:, data:, file:, etc.
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("Enter a full link starting with http:// or https://")
    return url


def validate_alias(alias: str) -> str:
    if not ALIAS_RE.fullmatch(alias):
        raise ValueError("Alias must be 3-32 characters: letters, numbers, - or _")
    if alias.lower() in RESERVED:
        raise ValueError("That alias is reserved")
    return alias


# ---------- links ----------
def generate_code(length: int = CODE_LENGTH) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))  # CSPRNG, unguessable


def create_link(conn, url: str, alias: str | None, expires_in_days: int | None) -> sqlite3.Row:
    now = utcnow()
    expires_at = iso(now + timedelta(days=expires_in_days)) if expires_in_days else None
    sql = "INSERT INTO links (code, original_url, created_at, expires_at) VALUES (?, ?, ?, ?)"
    # Custom alias: one attempt, a conflict is reported to the caller.
    # Random code: the UNIQUE constraint detects collisions, so retry.
    for _ in range(1 if alias else 5):
        code = alias or generate_code()
        try:
            conn.execute(sql, (code, url, iso(now), expires_at))
            return get_link(conn, code)
        except sqlite3.IntegrityError:
            continue
    raise AliasTaken(alias or "code generation failed")


def get_link(conn, code: str):
    return conn.execute("SELECT * FROM links WHERE code = ?", (code,)).fetchone()


def is_expired(link, now: datetime) -> bool:
    return link["expires_at"] is not None and link["expires_at"] <= iso(now)


# ---------- click tracking ----------
def parse_user_agent(ua: str) -> tuple[str, str]:
    """Deliberately simple substring rules; good enough for coarse analytics."""
    low = ua.lower()
    if not ua or any(k in low for k in ("bot", "crawler", "spider", "preview", "curl", "python-requests")):
        return "bot", "Other"
    if "ipad" in low or "tablet" in low:
        device = "tablet"
    elif "mobi" in low or "android" in low or "iphone" in low:
        device = "mobile"
    else:
        device = "desktop"
    # Order matters: Edge and Chrome UAs also contain "Safari".
    for token, name in (("edg/", "Edge"), ("opr/", "Opera"), ("firefox/", "Firefox"),
                        ("chrome/", "Chrome"), ("crios/", "Chrome"), ("safari/", "Safari")):
        if token in low:
            return device, name
    return device, "Other"


def referrer_host(referer: str | None) -> str:
    host = urlparse(referer).hostname if referer else None
    return host or "direct"


def record_click(db_path: str, link_id: int, referer, user_agent: str, ip: str, salt: str) -> None:
    now = utcnow()
    device, browser = parse_user_agent(user_agent or "")
    # Salted hash that rotates daily: counts distinct visitors without storing IPs.
    visitor = hashlib.sha256(f"{salt}:{now.date()}:{ip}:{user_agent}".encode()).hexdigest()[:16]
    with db.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO clicks (link_id, clicked_at, referrer_host, device, browser, visitor_hash)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (link_id, iso(now), referrer_host(referer), device, browser, visitor),
        )


# ---------- analytics ----------
def _breakdown(conn, link_id: int, column: str, limit: int = 5) -> list[dict]:
    assert column in {"referrer_host", "device", "browser"}  # whitelist: column is interpolated
    rows = conn.execute(
        f"SELECT {column} AS name, COUNT(*) AS count FROM clicks"
        f" WHERE link_id = ? AND device != 'bot' GROUP BY {column} ORDER BY count DESC LIMIT ?",
        (link_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def get_stats(conn, link, now: datetime, days: int = 30) -> dict:
    lid = link["id"]
    scalar = lambda sql, *args: conn.execute(sql, args).fetchone()[0]
    human = "FROM clicks WHERE link_id = ? AND device != 'bot'"
    since = (now - timedelta(days=days - 1)).date()
    per_day = dict(conn.execute(
        f"SELECT substr(clicked_at, 1, 10), COUNT(*) {human} AND clicked_at >= ? GROUP BY 1",
        (lid, since.isoformat()),
    ).fetchall())
    series = []
    for i in range(days):  # zero-fill so the chart has no gaps
        day = (since + timedelta(days=i)).isoformat()
        series.append({"date": day, "count": per_day.get(day, 0)})
    return {
        "code": link["code"],
        "original_url": link["original_url"],
        "created_at": link["created_at"],
        "expires_at": link["expires_at"],
        "total_clicks": scalar(f"SELECT COUNT(*) {human}", lid),
        "unique_visitors": scalar(f"SELECT COUNT(DISTINCT visitor_hash) {human}", lid),
        "bot_clicks": scalar("SELECT COUNT(*) FROM clicks WHERE link_id = ? AND device = 'bot'", lid),
        "clicks_by_day": series,
        "top_referrers": _breakdown(conn, lid, "referrer_host"),
        "devices": _breakdown(conn, lid, "device"),
        "browsers": _breakdown(conn, lid, "browser"),
    }
