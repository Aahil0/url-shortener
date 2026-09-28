import pytest
from fastapi.testclient import TestClient

from app import db, services
from app.config import Settings
from app.main import create_app

CHROME_DESKTOP = "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1"
BOT = "Slackbot-LinkExpanding 1.0"
TARGET = "https://example.com/some/long/path?x=1"


def make_client(tmp_path, **overrides):
    settings = Settings(database_path=str(tmp_path / "test.db"), visitor_salt="test-salt",
                        rate_limit_per_min=overrides.pop("rate_limit_per_min", 0), **overrides)
    return TestClient(create_app(settings), follow_redirects=False), settings


@pytest.fixture
def client(tmp_path):
    return make_client(tmp_path)[0]


def create(client, **body):
    return client.post("/api/links", json={"url": TARGET, **body})


def test_create_and_redirect(client):
    res = create(client)
    assert res.status_code == 201
    link = res.json()
    assert len(link["code"]) == services.CODE_LENGTH
    assert link["short_url"].endswith("/" + link["code"])
    redirect = client.get("/" + link["code"])
    assert redirect.status_code == 302
    assert redirect.headers["location"] == TARGET
    assert redirect.headers["cache-control"] == "no-store"


def test_custom_alias_and_conflict(client):
    assert create(client, custom_alias="my-launch").json()["code"] == "my-launch"
    assert create(client, custom_alias="my-launch").status_code == 409


@pytest.mark.parametrize("alias", ["ab", "has space", "api", "DOCS", "x" * 33])
def test_bad_aliases_rejected(client, alias):
    assert create(client, custom_alias=alias).status_code == 422


@pytest.mark.parametrize("url", ["javascript:alert(1)", "ftp://example.com/x", "example.com",
                                 "https://", "https://exa mple.com", "https://e.com/" + "a" * 2100])
def test_bad_urls_rejected(client, url):
    assert client.post("/api/links", json={"url": url}).status_code == 422


def test_cannot_shorten_own_domain(client):
    assert client.post("/api/links", json={"url": "http://testserver/abc"}).status_code == 422


def test_unknown_code_404(client):
    assert client.get("/nope123").status_code == 404
    assert client.get("/api/links/nope123/stats").status_code == 404


def test_expired_link_returns_410(tmp_path):
    client, settings = make_client(tmp_path)
    code = create(client, expires_in_days=1).json()["code"]
    assert client.get("/" + code).status_code == 302
    with db.connect(settings.database_path) as conn:
        conn.execute("UPDATE links SET expires_at = '2000-01-01T00:00:00Z'")
    assert client.get("/" + code).status_code == 410


def test_click_analytics(client):
    code = create(client).json()["code"]
    client.get("/" + code, headers={"user-agent": CHROME_DESKTOP, "referer": "https://news.ycombinator.com/item?id=1"})
    client.get("/" + code, headers={"user-agent": CHROME_DESKTOP, "referer": "https://news.ycombinator.com/x"})
    client.get("/" + code, headers={"user-agent": IPHONE})
    client.get("/" + code, headers={"user-agent": BOT})
    stats = client.get(f"/api/links/{code}/stats").json()
    assert stats["total_clicks"] == 3            # the bot is excluded from human totals
    assert stats["bot_clicks"] == 1
    assert stats["unique_visitors"] == 2         # same UA+IP twice, plus one iPhone
    assert stats["top_referrers"][0] == {"name": "news.ycombinator.com", "count": 2}
    assert {d["name"]: d["count"] for d in stats["devices"]} == {"desktop": 2, "mobile": 1}
    assert len(stats["clicks_by_day"]) == 30 and stats["clicks_by_day"][-1]["count"] == 3


def test_rate_limit(tmp_path):
    client, _ = make_client(tmp_path, rate_limit_per_min=3)
    assert [create(client).status_code for _ in range(3)] == [201, 201, 201]
    blocked = create(client)
    assert blocked.status_code == 429 and "retry-after" in blocked.headers


def test_health_and_index(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert "Shorten a link" in client.get("/").text
