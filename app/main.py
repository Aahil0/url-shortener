"""HTTP layer: routes only. Logic lives in services.py, SQL setup in db.py."""
from pathlib import Path
from urllib.parse import urlparse

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from . import db, services
from .config import Settings
from .ratelimit import RateLimiter
from .schemas import CreateLinkRequest, LinkResponse, StatsResponse

INDEX_HTML = Path(__file__).parent / "static" / "index.html"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    db.init_db(settings.database_path)
    limiter = RateLimiter(settings.rate_limit_per_min)
    app = FastAPI(title="Shortly", version="1.0.0",
                  description="URL shortener with privacy-friendly click analytics.")

    def client_ip(request: Request) -> str:
        # X-Forwarded-For is client-controlled; only trust it behind your own proxy.
        if settings.trust_proxy and (fwd := request.headers.get("x-forwarded-for")):
            return fwd.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def base_url(request: Request) -> str:
        return settings.base_url or str(request.base_url).rstrip("/")

    def link_out(row, request: Request) -> LinkResponse:
        return LinkResponse(code=row["code"], short_url=f"{base_url(request)}/{row['code']}",
                            original_url=row["original_url"], created_at=row["created_at"],
                            expires_at=row["expires_at"])

    def find_link(conn, code: str):
        link = services.get_link(conn, code)
        if link is None:
            raise HTTPException(404, "Link not found")
        return link

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(INDEX_HTML)

    @app.get("/health", include_in_schema=False)
    def health():
        with db.connect(settings.database_path) as conn:
            conn.execute("SELECT 1")
        return {"status": "ok"}

    @app.post("/api/links", response_model=LinkResponse, status_code=201, tags=["links"])
    def create_link(body: CreateLinkRequest, request: Request):
        allowed, retry_after = limiter.allow(client_ip(request))
        if not allowed:
            raise HTTPException(429, "Too many links created. Try again shortly.",
                                headers={"Retry-After": str(retry_after)})
        if urlparse(body.url).hostname == urlparse(base_url(request)).hostname:
            raise HTTPException(422, "You can't shorten a link that points to this service")
        with db.connect(settings.database_path) as conn:
            try:
                row = services.create_link(conn, body.url, body.custom_alias, body.expires_in_days)
            except services.AliasTaken:
                raise HTTPException(409, "That alias is already taken")
        return link_out(row, request)

    @app.get("/api/links/{code}", response_model=LinkResponse, tags=["links"])
    def get_link(code: str, request: Request):
        with db.connect(settings.database_path) as conn:
            return link_out(find_link(conn, code), request)

    @app.get("/api/links/{code}/stats", response_model=StatsResponse, tags=["analytics"])
    def get_stats(code: str):
        with db.connect(settings.database_path) as conn:
            return services.get_stats(conn, find_link(conn, code), services.utcnow())

    # Registered last so it can't shadow the routes above.
    @app.get("/{code}", include_in_schema=False)
    def follow(code: str, request: Request, background: BackgroundTasks):
        with db.connect(settings.database_path) as conn:
            link = find_link(conn, code)
        if services.is_expired(link, services.utcnow()):
            raise HTTPException(410, "This link has expired")
        # Record after the response is sent so analytics never slow the redirect.
        background.add_task(services.record_click, settings.database_path, link["id"],
                            request.headers.get("referer"), request.headers.get("user-agent", ""),
                            client_ip(request), settings.visitor_salt)
        # 302, not 301: browsers cache 301s and later clicks would never reach us.
        return RedirectResponse(link["original_url"], status_code=302,
                                headers={"Cache-Control": "no-store"})

    return app
