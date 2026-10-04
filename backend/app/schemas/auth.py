"""API shapes for accounts and sessions."""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.db.models.user import normalise_email

# Deliberately loose: the only real test of an address is sending mail to it. This catches
# typos without rejecting valid but unusual addresses.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

MIN_PASSWORD = 8
MAX_PASSWORD = 128  # Argon2 hashes any length, but unbounded input is a denial-of-service lever


class _EmailIn(BaseModel):
    email: str = Field(max_length=254)

    @field_validator("email")
    @classmethod
    def _valid_email(cls, value: str) -> str:
        value = normalise_email(value)
        if not _EMAIL.match(value):
            raise ValueError("Enter a valid email address.")
        return value


class RegisterIn(_EmailIn):
    password: str = Field(min_length=MIN_PASSWORD, max_length=MAX_PASSWORD)
    full_name: str | None = Field(default=None, max_length=120)


class LoginIn(_EmailIn):
    # No minimum here: login must say "incorrect", not "too short", or it leaks the policy.
    password: str = Field(max_length=MAX_PASSWORD)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(max_length=MAX_PASSWORD)
    new_password: str = Field(min_length=MIN_PASSWORD, max_length=MAX_PASSWORD)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str | None
    is_admin: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenOut(BaseModel):
    """The access token. The refresh token travels only as an httpOnly cookie."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class UsageOut(BaseModel):
    analyses: int
    llm_calls: int
    input_tokens: int
    output_tokens: int


class LimitsOut(BaseModel):
    analyses_per_day: int
    llm_calls_per_day: int
    tokens_per_day: int


class MeOut(BaseModel):
    user: UserOut
    usage_today: UsageOut
    limits: LimitsOut
    resets_at: datetime
