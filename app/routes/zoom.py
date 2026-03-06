# =====================================================
# ZOOM ROUTES
# OAuth connect flow + meeting scheduling endpoint
# =====================================================

import base64
import json
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from loguru import logger

from app.config.settings import settings
from app.middleware.auth import get_current_user
from app.middleware.error_handler import BadRequestError, NotFoundError
from app.models.schemas import User, ApiResponse
from app.config.database import get_supabase_admin
from app.services.zoom import (
    get_zoom_auth_url,
    exchange_code_for_tokens,
    store_zoom_tokens,
    is_zoom_connected,
    create_zoom_meeting,
)

router = APIRouter()


# ─────────────────────────────────────────────────────
# GET /api/zoom/auth/url
# ─────────────────────────────────────────────────────

@router.get("/auth/url", response_model=ApiResponse)
async def zoom_auth_url(user: User = Depends(get_current_user)):
    """Return the Zoom OAuth authorization URL for the authenticated mentor."""
    if not settings.zoom_client_id:
        raise BadRequestError("Zoom integration is not configured on this server.")
    url = get_zoom_auth_url(user.id)
    return {"success": True, "data": {"auth_url": url}}


# ─────────────────────────────────────────────────────
# GET /api/zoom/auth/callback
# ─────────────────────────────────────────────────────

@router.get("/auth/callback")
async def zoom_auth_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    """Handle Zoom OAuth callback — exchange code for tokens and redirect."""
    frontend = settings.frontend_url.rstrip("/")

    if error:
        logger.warning(f"Zoom OAuth error: {error}")
        return RedirectResponse(url=f"{frontend}/?zoom_error={error}")

    if not code:
        return RedirectResponse(url=f"{frontend}/?zoom_error=missing_code")

    # Decode state → get user_id
    user_id: Optional[str] = None
    if state:
        try:
            decoded = json.loads(base64.b64decode(state).decode())
            user_id = decoded.get("userId")
        except Exception:
            pass

    if not user_id:
        return RedirectResponse(url=f"{frontend}/?zoom_error=invalid_state")

    try:
        tokens = await exchange_code_for_tokens(code)
        store_zoom_tokens(user_id, tokens)
        logger.info(f"✅ Zoom connected for user {user_id}")
        return RedirectResponse(url=f"{frontend}/?zoom_connected=true")
    except Exception as exc:
        logger.error(f"Zoom token exchange failed: {exc}")
        return RedirectResponse(url=f"{frontend}/?zoom_error=token_exchange_failed")


# ─────────────────────────────────────────────────────
# GET /api/zoom/connection
# ─────────────────────────────────────────────────────

@router.get("/connection", response_model=ApiResponse)
async def check_zoom_connection(user: User = Depends(get_current_user)):
    """Check whether the current user has connected their Zoom account."""
    connected = is_zoom_connected(user.id)
    return {"success": True, "data": {"connected": connected}}


# ─────────────────────────────────────────────────────
# POST /api/zoom/schedule-session
# ─────────────────────────────────────────────────────

class ScheduleSessionRequest(BaseModel):
    request_id: str = Field(..., description="Mentor request ID (accepted)")
    start_time: str = Field(..., description="ISO 8601 datetime, e.g. 2026-03-10T14:00:00Z")
    duration_minutes: int = Field(default=60, ge=15, le=240)
    notes: Optional[str] = Field(None, description="Optional agenda / notes")


@router.post("/schedule-session", response_model=ApiResponse)
async def schedule_session_with_zoom(
    body: ScheduleSessionRequest,
    user: User = Depends(get_current_user),
):
    """
    Mentor-initiated: create a Zoom meeting for an accepted request and
    save a session record to the database.

    Flow:
    1. Verify mentor owns the request and it is 'accepted'
    2. Create a Zoom meeting via the mentor's OAuth tokens
    3. Insert a session row with zoom_link = join_url
    4. Update request status → 'scheduled'
    5. Notify the mentee
    """
    if user.role.value != "mentor":
        raise BadRequestError("Only mentors can schedule sessions")

    supabase = get_supabase_admin()

    # ── 1. Load & validate the request ──────────────────────────────────
    req_res = supabase.table("mentor_requests").select(
        "id, status, mentee_id, accepted_mentor_id, bounty, topic, title"
    ).eq("id", body.request_id).single().execute()

    if not req_res.data:
        raise NotFoundError("Request not found")

    req = req_res.data
    if req["accepted_mentor_id"] != user.id:
        raise BadRequestError("You are not the accepted mentor for this request")
    if req["status"] not in ("accepted", "paid"):
        raise BadRequestError(f"Request must be accepted before scheduling (current: {req['status']})")

    # ── 2. Create Zoom meeting ───────────────────────────────────────────
    topic = req.get("topic") or req.get("title") or "Mentorship Session"
    zoom_meeting = await create_zoom_meeting(
        user_id=user.id,
        topic=f"Avittam Session: {topic}",
        start_time=body.start_time,
        duration_minutes=body.duration_minutes,
        agenda=body.notes or topic,
    )

    join_url: str = zoom_meeting["join_url"]
    start_url: str = zoom_meeting.get("start_url", join_url)
    zoom_meeting_id: str = str(zoom_meeting["id"])

    # ── 3. Create session row ────────────────────────────────────────────
    session_insert = {
        "mentor_id": user.id,
        "mentee_id": req["mentee_id"],
        "request_id": body.request_id,
        "scheduled_at": body.start_time,
        "duration_minutes": body.duration_minutes,
        "status": "scheduled",
        "meeting_url": join_url,
        "zoom_meeting_id": zoom_meeting_id,
        "zoom_join_url": join_url,
        "zoom_start_url": start_url,
    }
    if body.notes:
        session_insert["mentor_notes"] = body.notes

    sess_res = supabase.table("sessions").insert(session_insert).select().single().execute()
    if not sess_res.data:
        raise BadRequestError("Failed to create session record")

    session = sess_res.data

    # ── 4. Update request status ─────────────────────────────────────────
    supabase.table("mentor_requests").update({"status": "scheduled"}).eq(
        "id", body.request_id
    ).execute()

    # ── 5. Notify mentee ─────────────────────────────────────────────────
    mentor_res = supabase.table("users").select("name").eq("id", user.id).single().execute()
    mentor_name = (mentor_res.data or {}).get("name", "Your mentor")
    try:
        supabase.table("notifications").insert({
            "user_id": req["mentee_id"],
            "type": "session_scheduled",
            "title": "Session Scheduled! 🎉",
            "message": (
                f"{mentor_name} has scheduled your session on "
                f"{body.start_time[:10]}. A Zoom link is ready."
            ),
            "related_entity_type": "session",
            "related_entity_id": session["id"],
            "action_url": "/sessions",
        }).execute()
    except Exception as notif_err:
        logger.warning(f"Notification insert failed: {notif_err}")

    return {
        "success": True,
        "message": "Session scheduled with Zoom meeting",
        "data": {
            "session_id": session["id"],
            "zoom_meeting_id": zoom_meeting_id,
            "join_url": join_url,
            "start_url": start_url,
            "scheduled_at": body.start_time,
        },
    }
