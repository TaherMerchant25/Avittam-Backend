# =====================================================
# ZOOM SERVICE
# Handles OAuth token management and meeting creation
# via the Zoom REST API v2.
# =====================================================

import base64
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from loguru import logger

from app.config.database import get_supabase_admin
from app.config.settings import settings

ZOOM_API_BASE = "https://api.zoom.us/v2"
ZOOM_AUTH_BASE = "https://zoom.us/oauth"


# ─────────────────────────────────────────────────────
# OAuth helpers
# ─────────────────────────────────────────────────────

def _basic_auth_header() -> str:
    """Build HTTP Basic header from Zoom client credentials."""
    raw = f"{settings.zoom_client_id}:{settings.zoom_client_secret}"
    return "Basic " + base64.b64encode(raw.encode()).decode()


def get_zoom_auth_url(user_id: str) -> str:
    """Return the Zoom OAuth authorization URL for this user."""
    from urllib.parse import urlencode
    # Use URL-safe base64 without padding so = / + / / chars never corrupt the state param
    state = base64.urlsafe_b64encode(json.dumps({"userId": user_id}).encode()).decode().rstrip("=")
    params = {
        "response_type": "code",
        "client_id": settings.zoom_client_id,
        "redirect_uri": settings.zoom_redirect_uri,
        "state": state,
    }
    return f"{ZOOM_AUTH_BASE}/authorize?{urlencode(params)}"


async def exchange_code_for_tokens(code: str) -> dict:
    """Exchange an auth code for access + refresh tokens."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{ZOOM_AUTH_BASE}/token",
            headers={"Authorization": _basic_auth_header()},
            params={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.zoom_redirect_uri,
            },
        )
        if not resp.is_success:
            logger.error(f"Zoom token exchange HTTP {resp.status_code}: {resp.text}")
        resp.raise_for_status()
        return resp.json()


async def _refresh_tokens(refresh_token: str) -> dict:
    """Obtain a fresh access token using the refresh token."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{ZOOM_AUTH_BASE}/token",
            headers={"Authorization": _basic_auth_header()},
            params={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
        resp.raise_for_status()
        return resp.json()


# ─────────────────────────────────────────────────────
# Token persistence (Supabase table: user_zoom_tokens)
# ─────────────────────────────────────────────────────

def store_zoom_tokens(user_id: str, tokens: dict) -> None:
    """Upsert Zoom tokens for a user."""
    supabase = get_supabase_admin()
    expires_in = tokens.get("expires_in", 3600)
    expiry = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()

    supabase.table("user_zoom_tokens").upsert(
        {
            "user_id": user_id,
            "access_token": tokens["access_token"],
            "refresh_token": tokens.get("refresh_token"),
            "expiry": expiry,
        },
        on_conflict="user_id",
    ).execute()
    logger.info(f"✅ Zoom tokens stored for user {user_id}")


async def get_valid_tokens(user_id: str) -> Optional[dict]:
    """Return valid tokens, auto-refreshing if expired. Returns None if not connected."""
    supabase = get_supabase_admin()
    result = supabase.table("user_zoom_tokens").select("*").eq("user_id", user_id).single().execute()
    if not result.data:
        return None

    row = result.data
    expiry = datetime.fromisoformat(row["expiry"])
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)

    # Refresh if within 2 minutes of expiry
    if expiry - datetime.now(timezone.utc) < timedelta(minutes=2):
        if not row.get("refresh_token"):
            logger.warning(f"Zoom token expired and no refresh token for user {user_id}")
            return None
        try:
            new_tokens = await _refresh_tokens(row["refresh_token"])
            store_zoom_tokens(user_id, new_tokens)
            return new_tokens
        except Exception as exc:
            logger.warning(f"Failed to refresh Zoom token for {user_id}: {exc}")
            return None

    return row


def is_zoom_connected(user_id: str) -> bool:
    """Quick check: does the user have stored Zoom tokens?"""
    supabase = get_supabase_admin()
    result = supabase.table("user_zoom_tokens").select("user_id").eq("user_id", user_id).execute()
    return bool(result.data)


# ─────────────────────────────────────────────────────
# Meeting creation
# ─────────────────────────────────────────────────────

async def create_zoom_meeting(
    user_id: str,
    topic: str,
    start_time: str,  # ISO 8601 string e.g. "2026-03-10T14:00:00Z"
    duration_minutes: int = 60,
    agenda: str = "",
) -> dict:
    """
    Create a Zoom meeting on behalf of the user.
    Returns the full Zoom meeting object (includes join_url, start_url, etc.).
    Raises ValueError if Zoom is not connected.
    """
    tokens = await get_valid_tokens(user_id)
    if not tokens:
        raise ValueError(
            "Zoom account not connected. Please connect Zoom from your dashboard first."
        )

    payload = {
        "topic": topic,
        "type": 2,  # Scheduled meeting
        "start_time": start_time,
        "duration": duration_minutes,
        "agenda": agenda,
        "settings": {
            "host_video": True,
            "participant_video": True,
            "join_before_host": False,
            "mute_upon_entry": False,
            "waiting_room": True,
            "auto_recording": "none",
        },
    }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{ZOOM_API_BASE}/users/me/meetings",
            headers={
                "Authorization": f"Bearer {tokens['access_token']}",
                "Content-Type": "application/json",
            },
            json=payload,
        )

        # Token expired mid-session — refresh and retry once
        if resp.status_code == 401 and tokens.get("refresh_token"):
            logger.info("Zoom token expired during meeting creation — refreshing…")
            new_tokens = await _refresh_tokens(tokens["refresh_token"])
            store_zoom_tokens(user_id, new_tokens)
            resp = await client.post(
                f"{ZOOM_API_BASE}/users/me/meetings",
                headers={
                    "Authorization": f"Bearer {new_tokens['access_token']}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

        if not resp.is_success:
            logger.error(f"Zoom meeting creation failed: {resp.status_code} {resp.text}")
            resp.raise_for_status()

        data = resp.json()
        logger.info(f"✅ Zoom meeting created: {data.get('id')} — {data.get('join_url')}")
        return data
