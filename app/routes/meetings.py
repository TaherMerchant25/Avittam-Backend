# =====================================================
# MEETINGS ROUTES
# HTTP endpoints for Google Meet integration
# =====================================================

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from typing import Optional
import base64
import json

from app.middleware.auth import get_current_user
from app.models.schemas import User, CalendarEventInput, ApiResponse
from app.config.google import get_google_auth_url, get_google_tokens
from app.config.settings import settings
from app.services.google_meet import (
    store_user_google_tokens,
    get_user_google_tokens,
    create_google_meet_session,
    get_user_calendar_events,
    check_calendar_availability,
    cancel_google_meet_session,
)
from app.middleware.error_handler import BadRequestError


router = APIRouter()


@router.get("/auth/url", response_model=ApiResponse)
async def get_google_auth_url_handler(
    user: User = Depends(get_current_user)
):
    """Get Google OAuth URL for user to connect their calendar"""
    # Include user ID in state for callback
    state = base64.b64encode(json.dumps({"userId": user.id}).encode()).decode()
    auth_url = get_google_auth_url(state)
    
    return {
        "success": True,
        "data": {"auth_url": auth_url},
        "message": "Redirect user to this URL to authorize Google Calendar access",
    }


@router.get("/auth/callback")
async def handle_google_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    """Handle Google OAuth callback"""
    if error:
        return RedirectResponse(
            url=f"{settings.frontend_url}/?google_error={error}"
        )
    
    if not code:
        raise BadRequestError("Authorization code is required")
    
    # Decode state to get user ID
    user_id = None
    if state:
        try:
            decoded = json.loads(base64.b64decode(state).decode())
            user_id = decoded.get("userId")
        except Exception:
            raise BadRequestError("Invalid state parameter")
    
    if not user_id:
        raise BadRequestError("User ID not found in state")
    
    # Exchange code for tokens
    tokens = await get_google_tokens(code)
    
    if not tokens.get("access_token"):
        raise BadRequestError("Failed to obtain access token")
    
    # Store tokens in database
    await store_user_google_tokens(user_id, tokens)
    
    # Redirect to frontend dashboard with success flag
    return RedirectResponse(
        url=f"{settings.frontend_url}/?google_connected=true"
    )


@router.get("/connection", response_model=ApiResponse)
async def check_google_connection(
    user: User = Depends(get_current_user)
):
    """Check if user has connected Google Calendar"""
    tokens = await get_user_google_tokens(user.id)
    
    return {
        "success": True,
        "data": {
            "connected": bool(tokens and tokens.get("access_token")),
            "has_refresh_token": bool(tokens and tokens.get("refresh_token")),
        },
    }


@router.post("", response_model=ApiResponse)
async def create_meeting(
    event: CalendarEventInput,
    user: User = Depends(get_current_user)
):
    """Create a Google Meet meeting manually"""
    if not event.summary or not event.start_time or not event.end_time or not event.attendees:
        raise BadRequestError("Summary, start_time, end_time, and attendees are required")
    
    meeting_details = await create_google_meet_session(user.id, {
        "summary": event.summary,
        "description": event.description,
        "start_time": event.start_time.isoformat(),
        "end_time": event.end_time.isoformat(),
        "attendees": event.attendees,
        "timezone": event.timezone,
    })
    
    return {
        "success": True,
        "data": meeting_details,
        "message": "Google Meet created successfully",
    }


@router.get("/calendar/events", response_model=ApiResponse)
async def get_calendar_events(
    max_results: int = Query(10, ge=1, le=50),
    user: User = Depends(get_current_user)
):
    """Get user's upcoming calendar events"""
    events = await get_user_calendar_events(user.id, max_results)
    
    formatted_events = []
    for event in events:
        formatted_event = {
            "id": event.get("id"),
            "summary": event.get("summary"),
            "description": event.get("description"),
            "start": event.get("start"),
            "end": event.get("end"),
            "meeting_url": None,
            "attendees": [],
        }
        
        # Extract meeting URL
        if event.get("conferenceData", {}).get("entryPoints"):
            video_entry = next(
                (e for e in event["conferenceData"]["entryPoints"] if e.get("entryPointType") == "video"),
                None
            )
            if video_entry:
                formatted_event["meeting_url"] = video_entry.get("uri")
        
        # Format attendees
        if event.get("attendees"):
            formatted_event["attendees"] = [
                {
                    "email": a.get("email"),
                    "name": a.get("displayName"),
                    "response_status": a.get("responseStatus"),
                }
                for a in event["attendees"]
            ]
        
        formatted_events.append(formatted_event)
    
    return {
        "success": True,
        "data": formatted_events,
    }


@router.get("/calendar/availability", response_model=ApiResponse)
async def check_availability(
    start_time: str,
    end_time: str,
    user: User = Depends(get_current_user)
):
    """Check calendar availability for a time slot"""
    if not start_time or not end_time:
        raise BadRequestError("start_time and end_time are required")
    
    is_available = await check_calendar_availability(user.id, start_time, end_time)
    
    return {
        "success": True,
        "data": {
            "available": is_available,
            "start_time": start_time,
            "end_time": end_time,
        },
    }


@router.delete("/{calendar_event_id}", response_model=ApiResponse)
async def cancel_meeting(
    calendar_event_id: str,
    user: User = Depends(get_current_user)
):
    """Cancel a Google Meet meeting"""
    await cancel_google_meet_session(user.id, calendar_event_id)
    
    return {
        "success": True,
        "message": "Meeting cancelled successfully",
    }


@router.delete("/connection", response_model=ApiResponse)
async def disconnect_google(
    user: User = Depends(get_current_user)
):
    """Disconnect Google Calendar"""
    from app.config.database import get_supabase_admin
    
    supabase = get_supabase_admin()
    supabase.table("user_google_tokens").delete().eq("user_id", user.id).execute()
    
    return {
        "success": True,
        "message": "Google Calendar disconnected successfully",
    }
