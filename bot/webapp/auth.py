from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from typing import Any, Optional
from urllib.parse import parse_qsl

import httpx

from bot.config import settings

logger = logging.getLogger(__name__)

_TRYCLOUDFLARE_RE = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
_cached_tunnel_url: Optional[str] = None
_cached_tunnel_ts: float = 0.0


def verify_telegram_init_data(
    init_data: str,
    bot_token: Optional[str] = None,
    max_age_seconds: int = 86400 * 7,
) -> Optional[dict[str, Any]]:
    """
    Validate Telegram Mini App `window.Telegram.WebApp.initData` using HMAC-SHA256.
    Returns parsed user info dict on success, or None if invalid/expired.
    """
    if not init_data or not init_data.strip():
        return None

    token = (bot_token or settings.bot_token or "").strip()
    if not token:
        return None

    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    except Exception:
        return None

    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(
        b"WebAppData", token.encode("utf-8"), hashlib.sha256
    ).digest()
    expected_hash = hmac.new(
        secret_key, data_check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    if not secrets.compare_digest(expected_hash, received_hash):
        return None

    auth_date_str = pairs.get("auth_date")
    if auth_date_str and max_age_seconds > 0:
        try:
            auth_date = int(auth_date_str)
            if int(time.time()) - auth_date > max_age_seconds:
                return None
        except ValueError:
            return None

    user_raw = pairs.get("user")
    if not user_raw:
        return None

    try:
        user_obj = json.loads(user_raw)
    except Exception:
        return None

    user_id = user_obj.get("id")
    if not isinstance(user_id, int):
        return None

    first = (user_obj.get("first_name") or "").strip()
    last = (user_obj.get("last_name") or "").strip()
    full_name = f"{first} {last}".strip() or user_obj.get("username") or f"User {user_id}"

    return {
        "user_id": user_id,
        "display_name": full_name,
        "username": user_obj.get("username"),
        "start_param": pairs.get("start_param"),
    }


def _signing_secret(secret: Optional[str] = None) -> bytes:
    raw = secret or settings.bot_token or "fallback-secret"
    return hashlib.sha256(f"tg_expense_dashboard:{raw}".encode("utf-8")).digest()


def create_dashboard_token(
    household_id: int,
    member_id: Optional[int] = None,
    telegram_user_id: Optional[int] = None,
    secret: Optional[str] = None,
    ttl_seconds: int = 86400 * 7,
) -> str:
    """
    Create an HMAC-SHA256 signed token granting access to a household's Live Dashboard.
    """
    payload = {
        "hh": int(household_id),
        "mem": int(member_id) if member_id is not None else None,
        "uid": int(telegram_user_id) if telegram_user_id is not None else None,
        "exp": int(time.time()) + int(ttl_seconds),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    b64_payload = base64.urlsafe_b64encode(payload_bytes).decode("ascii").rstrip("=")
    sig = hmac.new(
        _signing_secret(secret), b64_payload.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return f"{b64_payload}.{sig}"


def verify_dashboard_token(
    token: str,
    secret: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """
    Verify an HMAC-SHA256 signed dashboard token and return its payload if valid and unexpired.
    """
    if not token or "." not in token:
        return None
    parts = token.strip().split(".", 1)
    if len(parts) != 2:
        return None
    b64_payload, received_sig = parts
    expected_sig = hmac.new(
        _signing_secret(secret), b64_payload.encode("ascii"), hashlib.sha256
    ).hexdigest()
    if not secrets.compare_digest(expected_sig, received_sig):
        return None

    padding = "=" * (-len(b64_payload) % 4)
    try:
        raw_bytes = base64.urlsafe_b64decode(b64_payload + padding)
        payload = json.loads(raw_bytes.decode("utf-8"))
    except Exception:
        return None

    exp = payload.get("exp")
    if not isinstance(exp, int) or exp < int(time.time()):
        return None
    if not isinstance(payload.get("hh"), int):
        return None
    return payload


async def resolve_public_webapp_url() -> Optional[str]:
    """
    Return the public HTTPS URL for the Mini App:
    1. If `settings.webapp_url` is explicitly configured, return it.
    2. Otherwise, check the Cloudflare Quick Tunnel metrics endpoint (`settings.tunnel_metrics_url`)
       if running alongside a `cloudflared` sidecar container.
    """
    global _cached_tunnel_url, _cached_tunnel_ts

    if settings.webapp_url and settings.webapp_url.strip():
        return settings.webapp_url.strip().rstrip("/")

    now = time.monotonic()
    if _cached_tunnel_url and (now - _cached_tunnel_ts) < 60.0:
        return _cached_tunnel_url

    metrics_url = (settings.tunnel_metrics_url or "").strip()
    if not metrics_url:
        return None

    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            resp = await client.get(metrics_url)
            if resp.status_code == 200:
                match = _TRYCLOUDFLARE_RE.search(resp.text)
                if match:
                    _cached_tunnel_url = match.group(0).rstrip("/")
                    _cached_tunnel_ts = now
                    return _cached_tunnel_url
    except Exception as exc:
        logger.debug("Cloudflared tunnel metrics not reachable: %s", exc)

    return _cached_tunnel_url

