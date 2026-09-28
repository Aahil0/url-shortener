"""Request/response models. Pydantic validates input and documents the API."""
from pydantic import BaseModel, Field, field_validator

from . import services


class CreateLinkRequest(BaseModel):
    url: str
    custom_alias: str | None = None
    expires_in_days: int | None = Field(default=None, ge=1, le=365)

    @field_validator("url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        return services.validate_url(v)

    @field_validator("custom_alias")
    @classmethod
    def _check_alias(cls, v: str | None) -> str | None:
        return services.validate_alias(v.strip()) if v and v.strip() else None


class LinkResponse(BaseModel):
    code: str
    short_url: str
    original_url: str
    created_at: str
    expires_at: str | None


class Count(BaseModel):
    name: str
    count: int


class DayCount(BaseModel):
    date: str
    count: int


class StatsResponse(BaseModel):
    code: str
    original_url: str
    created_at: str
    expires_at: str | None
    total_clicks: int
    unique_visitors: int
    bot_clicks: int
    clicks_by_day: list[DayCount]
    top_referrers: list[Count]
    devices: list[Count]
    browsers: list[Count]
