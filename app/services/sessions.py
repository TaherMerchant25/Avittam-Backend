# =====================================================
# SESSION SERVICE
# Session booking and management
# =====================================================

from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from loguru import logger

from app.config.database import get_supabase_admin
from app.services.notifications import create_notification
from app.models.schemas import (
    Session,
    CreateSessionRequest,
    SessionStatus,
    SessionFilters,
)
from app.middleware.error_handler import NotFoundError, BadRequestError, ConflictError


async def create_session(
    input_data: CreateSessionRequest,
    create_meeting: bool = True
) -> Dict[str, Any]:
    """
    Create a new session with optional Google Meet.
    
    Args:
        input_data: Session creation data
        create_meeting: Whether to create a Google Meet link
        
    Returns:
        Created session data
    """
    supabase = get_supabase_admin()
    
    # Verify mentor exists and is a mentor
    mentor_result = supabase.table("users").select("id, email, name, role").eq(
        "id", input_data.mentor_id
    ).single().execute()
    
    if not mentor_result.data:
        raise NotFoundError("Mentor not found")
    
    mentor = mentor_result.data
    if mentor["role"] not in ["mentor", "admin"]:
        raise BadRequestError("Selected user is not a mentor")
    
    # Verify mentee exists
    mentee_result = supabase.table("users").select("id, email, name").eq(
        "id", input_data.mentee_id
    ).single().execute()
    
    if not mentee_result.data:
        raise NotFoundError("Mentee not found")
    
    mentee = mentee_result.data
    
    # Calculate end time
    duration = input_data.duration_minutes
    start_time = input_data.scheduled_at
    end_time = start_time + timedelta(minutes=duration)
    
    # Create session in database (Zoom meeting is created separately via /api/zoom/schedule-session)
    session_data = {
        "mentor_id": input_data.mentor_id,
        "mentee_id": input_data.mentee_id,
        "request_id": input_data.request_id,
        "scheduled_at": start_time.isoformat(),
        "duration_minutes": duration,
        "meeting_url": None,
        "status": SessionStatus.SCHEDULED.value,
    }
    
    result = supabase.table("sessions").insert(session_data).execute()
    
    if not result.data:
        raise Exception("Failed to create session")
    
    session = result.data[0]
    
    # Send notifications
    await create_notification({
        "user_id": input_data.mentor_id,
        "type": "session",
        "title": "New Session Scheduled",
        "message": f"You have a new session with {mentee['name']} on {start_time.strftime('%Y-%m-%d')}",
        "related_entity_type": "session",
        "related_entity_id": session["id"],
        "action_url": f"/sessions/{session['id']}",
    })
    
    await create_notification({
        "user_id": input_data.mentee_id,
        "type": "session",
        "title": "Session Confirmed",
        "message": f"Your session with {mentor['name']} is confirmed for {start_time.strftime('%Y-%m-%d')}",
        "related_entity_type": "session",
        "related_entity_id": session["id"],
        "action_url": f"/sessions/{session['id']}",
    })
    
    return session


async def get_session_by_id(session_id: str) -> Dict[str, Any]:
    """Get session by ID with user details"""
    supabase = get_supabase_admin()
    
    result = supabase.table("sessions").select(
        "*, mentor:users!sessions_mentor_id_fkey(id, name, email, avatar_url), "
        "mentee:users!sessions_mentee_id_fkey(id, name, email, avatar_url)"
    ).eq("id", session_id).single().execute()
    
    if not result.data:
        raise NotFoundError("Session not found")
    
    return result.data


async def get_user_sessions(
    user_id: str,
    filters: SessionFilters
) -> Dict[str, Any]:
    """
    Get sessions for a user (as mentor or mentee).
    
    Args:
        user_id: User ID
        filters: Session filters
        
    Returns:
        Sessions list with pagination
    """
    supabase = get_supabase_admin()
    
    query = supabase.table("sessions").select(
        "*, mentor:users!sessions_mentor_id_fkey(id, name, email, avatar_url), "
        "mentee:users!sessions_mentee_id_fkey(id, name, email, avatar_url)",
        count="exact"
    )
    
    # Filter by role
    if filters.role == "mentor":
        query = query.eq("mentor_id", user_id)
    elif filters.role == "mentee":
        query = query.eq("mentee_id", user_id)
    else:
        query = query.or_(f"mentor_id.eq.{user_id},mentee_id.eq.{user_id}")
    
    # Filter by status
    if filters.status:
        status_values = [s.value for s in filters.status]
        query = query.in_("status", status_values)
    
    # Filter by date range
    if filters.from_date:
        query = query.gte("scheduled_at", filters.from_date.isoformat())
    if filters.to_date:
        query = query.lte("scheduled_at", filters.to_date.isoformat())
    
    # Pagination
    offset = (filters.page - 1) * filters.limit
    query = query.order("scheduled_at").range(offset, offset + filters.limit - 1)
    
    result = query.execute()
    
    return {
        "sessions": result.data or [],
        "total": result.count or 0,
    }


async def update_session_status(
    session_id: str,
    status: SessionStatus,
    user_id: str
) -> Dict[str, Any]:
    """Update session status"""
    supabase = get_supabase_admin()
    
    # Verify session exists and user has access
    session = await get_session_by_id(session_id)
    
    if session["mentor_id"] != user_id and session["mentee_id"] != user_id:
        raise BadRequestError("You don't have access to this session")
    
    update_data = {"status": status.value, "updated_at": datetime.now().isoformat()}
    
    if status == SessionStatus.ONGOING:
        update_data["started_at"] = datetime.now().isoformat()
    elif status == SessionStatus.COMPLETED:
        update_data["ended_at"] = datetime.now().isoformat()
    
    result = supabase.table("sessions").update(update_data).eq("id", session_id).execute()
    
    if not result.data:
        raise Exception("Failed to update session")
    
    return result.data[0]


async def cancel_session(
    session_id: str,
    user_id: str,
    reason: Optional[str] = None
) -> Dict[str, Any]:
    """Cancel a session"""
    supabase = get_supabase_admin()
    
    session = await get_session_by_id(session_id)
    
    if session["mentor_id"] != user_id and session["mentee_id"] != user_id:
        raise BadRequestError("You don't have access to this session")
    
    if session["status"] in [SessionStatus.COMPLETED.value, SessionStatus.CANCELLED.value]:
        raise ConflictError("Cannot cancel this session")
    
    update_data = {
        "status": SessionStatus.CANCELLED.value,
        "updated_at": datetime.now().isoformat(),
    }
    
    result = supabase.table("sessions").update(update_data).eq("id", session_id).execute()
    
    # Notify other party
    other_user_id = session["mentee_id"] if session["mentor_id"] == user_id else session["mentor_id"]
    await create_notification({
        "user_id": other_user_id,
        "type": "session",
        "title": "Session Cancelled",
        "message": f"A session has been cancelled. Reason: {reason or 'No reason provided'}",
        "related_entity_type": "session",
        "related_entity_id": session_id,
    })
    
    return result.data[0]


async def reschedule_session(
    session_id: str,
    new_time: datetime,
    user_id: str
) -> Dict[str, Any]:
    """Reschedule a session"""
    supabase = get_supabase_admin()
    
    session = await get_session_by_id(session_id)
    
    if session["mentor_id"] != user_id and session["mentee_id"] != user_id:
        raise BadRequestError("You don't have access to this session")
    
    if session["status"] != SessionStatus.SCHEDULED.value:
        raise ConflictError("Can only reschedule scheduled sessions")
    
    update_data = {
        "scheduled_at": new_time.isoformat(),
        "updated_at": datetime.now().isoformat(),
    }
    
    result = supabase.table("sessions").update(update_data).eq("id", session_id).execute()
    
    # Notify other party
    other_user_id = session["mentee_id"] if session["mentor_id"] == user_id else session["mentor_id"]
    await create_notification({
        "user_id": other_user_id,
        "type": "session",
        "title": "Session Rescheduled",
        "message": f"A session has been rescheduled to {new_time.strftime('%Y-%m-%d %H:%M')}",
        "related_entity_type": "session",
        "related_entity_id": session_id,
    })
    
    return result.data[0]


async def add_session_notes(
    session_id: str,
    user_id: str,
    notes: str,
    is_mentor: bool
) -> Dict[str, Any]:
    """Add notes to a session"""
    supabase = get_supabase_admin()
    
    session = await get_session_by_id(session_id)
    
    if session["mentor_id"] != user_id and session["mentee_id"] != user_id:
        raise BadRequestError("You don't have access to this session")
    
    field = "mentor_notes" if is_mentor else "mentee_notes"
    update_data = {
        field: notes,
        "updated_at": datetime.now().isoformat(),
    }
    
    result = supabase.table("sessions").update(update_data).eq("id", session_id).execute()
    
    return result.data[0]


async def get_upcoming_sessions(hours_ahead: int = 24) -> List[Dict[str, Any]]:
    """Get sessions starting within specified hours"""
    supabase = get_supabase_admin()
    
    now = datetime.now()
    cutoff = now + timedelta(hours=hours_ahead)
    
    result = supabase.table("sessions").select(
        "*, mentor:users!sessions_mentor_id_fkey(id, name, email), "
        "mentee:users!sessions_mentee_id_fkey(id, name, email)"
    ).eq("status", SessionStatus.SCHEDULED.value).gte(
        "scheduled_at", now.isoformat()
    ).lte("scheduled_at", cutoff.isoformat()).order("scheduled_at").execute()
    
    return result.data or []
