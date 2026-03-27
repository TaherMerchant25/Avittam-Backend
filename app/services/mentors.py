# =====================================================
# MENTOR SERVICE
# Ping system, broadcast, and mentor management
# =====================================================

from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from loguru import logger

from app.config.database import get_supabase_admin
from app.services.notifications import create_notification
from app.models.schemas import (
    MentorRequest,
    CreateMentorRequestInput,
    BroadcastPingInput,
    RequestStatus,
    RequestFilters,
    MentorFilters,
)
from app.middleware.error_handler import NotFoundError, BadRequestError, ConflictError, ForbiddenError


# Lock duration in minutes - how long a mentor can hold a request
LOCK_DURATION_MINUTES = 15

# Request expiration in hours
REQUEST_EXPIRATION_HOURS = 24


# =====================================================
# MENTOR REQUEST (PING) OPERATIONS
# =====================================================

async def create_mentor_request(
    mentee_id: str,
    input_data: CreateMentorRequestInput
) -> Dict[str, Any]:
    """Create a new mentor request (ping)"""
    supabase = get_supabase_admin()
    
    # Verify mentee exists
    mentee_result = supabase.table("users").select("id, name, email").eq(
        "id", mentee_id
    ).single().execute()
    
    if not mentee_result.data:
        raise NotFoundError("Mentee not found")
    
    expires_at = datetime.now() + timedelta(hours=REQUEST_EXPIRATION_HOURS)
    
    request_data = {
        "mentee_id": mentee_id,
        "title": input_data.title,
        "description": input_data.description,
        "topic": input_data.topic,
        "mentorship_type": input_data.mentorship_type.value,
        "plan_id": input_data.plan_id,
        "bounty": input_data.bounty,
        "preferred_date": input_data.preferred_date.isoformat() if input_data.preferred_date else None,
        "duration_minutes": input_data.duration_minutes,
        "status": RequestStatus.PENDING.value,
        "expires_at": expires_at.isoformat(),
    }
    
    result = supabase.table("mentor_requests").insert(request_data).execute()
    
    if not result.data:
        raise Exception("Failed to create mentor request")
    
    return result.data[0]


async def broadcast_ping(
    mentee_id: str,
    input_data: BroadcastPingInput
) -> Dict[str, Any]:
    """
    Broadcast a ping to mentors. Creates request in DB - the trigger
    broadcast_request_to_mentors() automatically notifies all mentors.
    Returns immediately for fast UX.
    """
    request = await create_mentor_request(mentee_id, CreateMentorRequestInput(
        title=input_data.title,
        description=input_data.description,
        topic=input_data.topic,
        mentorship_type=input_data.mentorship_type,
        bounty=input_data.bounty,
        preferred_date=input_data.preferred_date,
        duration_minutes=input_data.duration_minutes,
    ))

    # Count mentors for response (lightweight)
    supabase = get_supabase_admin()
    count_result = supabase.table("users").select("id", count="exact").eq(
        "role", "mentor"
    ).eq("is_active", True).execute()
    mentor_count = count_result.count or 0

    return {
        "request": request,
        "notified_mentors": mentor_count,
    }


async def get_pending_requests(filters: RequestFilters) -> Dict[str, Any]:
    """Get all pending requests for mentors to browse"""
    supabase = get_supabase_admin()
    
    query = supabase.table("mentor_requests").select(
        "*, mentee:users!mentor_requests_mentee_id_fkey(id, name, avatar_url)",
        count="exact"
    ).eq("status", RequestStatus.PENDING.value).gt(
        "expires_at", datetime.now().isoformat()
    )
    
    if filters.topic:
        query = query.ilike("topic", f"%{filters.topic}%")
    
    if filters.mentorship_type:
        query = query.eq("mentorship_type", filters.mentorship_type.value)
    
    if filters.min_bounty:
        query = query.gte("bounty", filters.min_bounty)
    
    offset = (filters.page - 1) * filters.limit
    query = query.order("created_at", desc=True).range(offset, offset + filters.limit - 1)
    
    result = query.execute()
    
    return {
        "requests": result.data or [],
        "total": result.count or 0,
    }


async def lock_request(request_id: str, mentor_id: str) -> Dict[str, Any]:
    """Lock a request (mentor claims it temporarily)"""
    supabase = get_supabase_admin()
    
    # Get current request state
    result = supabase.table("mentor_requests").select("*").eq("id", request_id).single().execute()
    
    if not result.data:
        raise NotFoundError("Request not found")
    
    request = result.data
    
    # Check if request is available
    if request["status"] != RequestStatus.PENDING.value:
        raise ConflictError(f"Request is {request['status']}, cannot be locked")
    
    # Check if request is expired
    if datetime.fromisoformat(request["expires_at"].replace("Z", "+00:00")) < datetime.now():
        raise BadRequestError("Request has expired")
    
    # Check if already locked by another mentor
    if request.get("locked_by") and request["locked_by"] != mentor_id:
        lock_expires = datetime.fromisoformat(request["lock_expires_at"].replace("Z", "+00:00"))
        if lock_expires > datetime.now():
            raise ConflictError("Request is already locked by another mentor")
    
    # Set lock
    lock_expires_at = datetime.now() + timedelta(minutes=LOCK_DURATION_MINUTES)
    
    update_result = supabase.table("mentor_requests").update({
        "status": RequestStatus.LOCKED.value,
        "locked_by": mentor_id,
        "locked_at": datetime.now().isoformat(),
        "lock_expires_at": lock_expires_at.isoformat(),
        "updated_at": datetime.now().isoformat(),
    }).eq("id", request_id).execute()
    
    return update_result.data[0]


async def unlock_request(request_id: str, mentor_id: str) -> Dict[str, Any]:
    """Unlock a request"""
    supabase = get_supabase_admin()
    
    result = supabase.table("mentor_requests").select("*").eq("id", request_id).single().execute()
    
    if not result.data:
        raise NotFoundError("Request not found")
    
    request = result.data
    
    if request.get("locked_by") != mentor_id:
        raise ForbiddenError("You don't have a lock on this request")
    
    update_result = supabase.table("mentor_requests").update({
        "status": RequestStatus.PENDING.value,
        "locked_by": None,
        "locked_at": None,
        "lock_expires_at": None,
        "updated_at": datetime.now().isoformat(),
    }).eq("id", request_id).execute()
    
    return update_result.data[0]


async def accept_request(request_id: str, mentor_id: str) -> Dict[str, Any]:
    """
    Accept a request. Automatically creates a session placeholder and
    unlocks a chat channel so mentor and mentee can discuss immediately.
    """
    supabase = get_supabase_admin()

    result = supabase.table("mentor_requests").select("*").eq("id", request_id).single().execute()

    if not result.data:
        raise NotFoundError("Request not found")

    request = result.data
    mentee_id = request["mentee_id"]


    # Check if mentor has the lock
    if request.get("locked_by") != mentor_id:
        raise ForbiddenError("You must lock the request before accepting")

    update_result = supabase.table("mentor_requests").update({
        "status": RequestStatus.ACCEPTED.value,
        "accepted_by": mentor_id,
        "accepted_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }).eq("id", request_id).execute()

    # ── Create a session placeholder so chat can be attached immediately ──
    session_id: Optional[str] = None
    try:
        import uuid as _uuid
        session_id = str(_uuid.uuid4())
        supabase.table("sessions").insert({
            "id": session_id,
            "mentor_id": mentor_id,
            "mentee_id": mentee_id,
            "request_id": request_id,
            "status": "accepted",
            "duration_minutes": request.get("duration_minutes") or 60,
            # placeholder — will be updated when mentor formally schedules
            "scheduled_at": (datetime.now() + timedelta(days=7)).isoformat(),
        }).execute()
        logger.info(f"✅ Session placeholder created: {session_id}")
    except Exception as sess_err:
        logger.warning(f"Session placeholder creation failed (non-fatal): {sess_err}")

    # ── Auto-create chat channel so both parties can talk immediately ─────
    if session_id:
        try:
            from app.services import stream_chat as _sc
            if _sc.is_stream_chat_configured():
                mentor_info = supabase.table("users").select("name, avatar_url").eq("id", mentor_id).single().execute()
                mentee_info = supabase.table("users").select("name, avatar_url").eq("id", mentee_id).single().execute()
                m_data = mentor_info.data or {}
                s_data = mentee_info.data or {}

                _sc.ensure_system_user()
                _sc.upsert_stream_user(mentor_id, m_data.get("name", "Mentor"), m_data.get("avatar_url"), "mentor")
                _sc.upsert_stream_user(mentee_id, s_data.get("name", "Student"), s_data.get("avatar_url"), "mentee")

                topic = request.get("topic") or request.get("title") or "Mentorship Session"
                stream_channel_id = _sc.create_session_channel(session_id, mentor_id, mentee_id, topic)
                _sc.activate_channel(stream_channel_id)

                supabase.table("chat_channels").insert({
                    "session_id": session_id,
                    "mentor_id": mentor_id,
                    "mentee_id": mentee_id,
                    "stream_channel_id": stream_channel_id,
                    "is_active": True,
                }).execute()
                logger.info(f"✅ Chat channel auto-created for session {session_id}")
        except Exception as chat_err:
            logger.warning(f"Chat channel creation failed (non-fatal): {chat_err}")

    # ── Notify the mentee ─────────────────────────────────────────────────
    await create_notification({
        "user_id": mentee_id,
        "type": "request",
        "title": "🎉 Mentor Accepted Your Request!",
        "message": "A mentor accepted your request. You can now chat and discuss your session.",
        "related_entity_type": "mentor_request",
        "related_entity_id": request_id,
        "action_url": "/sessions",
    })

    return update_result.data[0]


async def cancel_request(request_id: str, mentee_id: str) -> Dict[str, Any]:
    """Cancel a request (mentee only)"""
    supabase = get_supabase_admin()
    
    result = supabase.table("mentor_requests").select("*").eq("id", request_id).single().execute()
    
    if not result.data:
        raise NotFoundError("Request not found")
    
    request = result.data
    
    if request["mentee_id"] != mentee_id:
        raise ForbiddenError("You can only cancel your own requests")
    
    if request["status"] == RequestStatus.ACCEPTED.value:
        raise ConflictError("Cannot cancel an accepted request")
    
    update_result = supabase.table("mentor_requests").update({
        "status": RequestStatus.CANCELLED.value,
        "updated_at": datetime.now().isoformat(),
    }).eq("id", request_id).execute()
    
    return update_result.data[0]


async def get_mentee_requests(
    mentee_id: str,
    status: Optional[List[RequestStatus]] = None
) -> List[Dict[str, Any]]:
    """Get mentee's own requests"""
    supabase = get_supabase_admin()
    
    query = supabase.table("mentor_requests").select("*").eq("mentee_id", mentee_id)
    
    if status:
        status_values = [s.value for s in status]
        query = query.in_("status", status_values)
    
    result = query.order("created_at", desc=True).execute()
    
    return result.data or []


async def record_request_view(request_id: str, mentor_id: str) -> None:
    """Record that a mentor viewed a request"""
    supabase = get_supabase_admin()
    
    # You could implement a views table here
    # For now, just log it
    logger.info(f"Mentor {mentor_id} viewed request {request_id}")


# =====================================================
# MENTOR DISCOVERY OPERATIONS
# =====================================================

async def get_active_mentors(filters: MentorFilters) -> Dict[str, Any]:
    """Get all active mentors"""
    supabase = get_supabase_admin()
    
    query = supabase.table("users").select(
        "id, name, email, avatar_url, user_profiles(headline, bio, expertise, hourly_rate, total_experience_years, linkedin_url, portfolio_url)",
        count="exact"
    ).eq("role", "mentor").eq("is_active", True)
    
    offset = (filters.page - 1) * filters.limit
    query = query.range(offset, offset + filters.limit - 1)
    
    result = query.execute()
    
    mentors = []
    for user in result.data or []:
        profile = user.get("user_profiles", [{}])
        if isinstance(profile, list):
            profile = profile[0] if profile else {}
        
        mentor = {
            "id": user["id"],
            "name": user["name"],
            "email": user["email"],
            "avatar_url": user.get("avatar_url"),
            "headline": profile.get("headline"),
            "bio": profile.get("bio"),
            "expertise": profile.get("expertise", []),
            "hourly_rate": profile.get("hourly_rate"),
            "total_experience_years": profile.get("total_experience_years"),
            "linkedin_url": profile.get("linkedin_url"),
            "portfolio_url": profile.get("portfolio_url"),
        }
        
        # Apply filters
        if filters.expertise:
            mentor_expertise = mentor.get("expertise") or []
            if not any(
                any(f.lower() in e.lower() for f in filters.expertise)
                for e in mentor_expertise
            ):
                continue
        
        if filters.max_hourly_rate and mentor.get("hourly_rate"):
            if mentor["hourly_rate"] > filters.max_hourly_rate:
                continue
        
        mentors.append(mentor)
    
    return {
        "mentors": mentors,
        "total": result.count or 0,
    }


async def get_mentor_by_id(mentor_id: str) -> Dict[str, Any]:
    """Get mentor details by ID"""
    supabase = get_supabase_admin()
    
    result = supabase.table("users").select(
        "id, name, email, avatar_url, user_profiles(headline, bio, expertise, hourly_rate, total_experience_years, linkedin_url, portfolio_url)"
    ).eq("id", mentor_id).eq("role", "mentor").single().execute()
    
    if not result.data:
        raise NotFoundError("Mentor not found")
    
    user = result.data
    profile = user.get("user_profiles", [{}])
    if isinstance(profile, list):
        profile = profile[0] if profile else {}
    
    return {
        "id": user["id"],
        "name": user["name"],
        "email": user["email"],
        "avatar_url": user.get("avatar_url"),
        **profile,
    }
