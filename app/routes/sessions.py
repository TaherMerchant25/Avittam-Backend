# =====================================================
# SESSION ROUTES
# HTTP endpoints for session management
# =====================================================

import hmac
import hashlib
from fastapi import APIRouter, Depends, Query
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel

from app.middleware.auth import get_current_user, require_mentee
from app.config.database import get_supabase_admin
from app.config.settings import settings
from app.services.jitsi import generate_meeting_url
from app.models.schemas import (
    User,
    CreateSessionRequest,
    SessionStatus,
    SessionFilters,
    UpdateSessionStatus,
    RescheduleSession,
    AddSessionNotes,
    ApiResponse,
    PaginatedResponse,
    PaginationInfo,
)
from app.services import sessions as session_service
from app.services import session_booking as booking_service
from app.services.wallets import credit_mentor_for_session_payment
from app.middleware.error_handler import BadRequestError, NotFoundError


router = APIRouter()


# =====================================================
# SESSION BOOKING (must be before /{session_id})
# =====================================================
class BookSessionBody(BaseModel):
    mentorId: str
    amountInr: float
    scheduledAt: Optional[str] = None
    durationMinutes: Optional[int] = None


class BookWithCoinsBody(BaseModel):
    mentorId: str
    totalCoins: float
    scheduledAt: Optional[str] = None
    durationMinutes: Optional[int] = None
    requestId: Optional[str] = None  # mentor_request row id — marked 'booked' after payment


@router.post("/book/coins")
async def book_session_with_coins(body: BookWithCoinsBody, user: User = Depends(require_mentee)):
    """Create session + pay with Avittam Coins + credit mentor + create chat immediately"""
    if user.role.value not in ("mentee", "admin"):
        raise BadRequestError("Only mentees can book sessions")
    if not body.mentorId or not body.totalCoins or body.totalCoins <= 0:
        raise BadRequestError("mentorId and totalCoins (positive) are required")

    supabase = get_supabase_admin()
    mentor_result = supabase.table("users").select("id, name, avatar_url").eq(
        "id", body.mentorId
    ).eq("is_active", True).single().execute()
    if not mentor_result.data:
        raise NotFoundError("Mentor not found or inactive")
    mentor = mentor_result.data

    mentee_result = supabase.table("users").select("name, avatar_url").eq("id", user.id).single().execute()
    mentee_data = mentee_result.data or {}

    session_id = booking_service.create_session(
        body.mentorId, user.id, body.scheduledAt, body.durationMinutes or 60
    )

    from app.services.wallets import pay_for_session_with_coins
    result = pay_for_session_with_coins(
        mentee_id=user.id,
        session_id=session_id,
        mentor_id=body.mentorId,
        total_coins=body.totalCoins,
        settle_immediately=True,
    )

    # Mark the originating mentor_request as 'booked' so it never resurfaces for the student
    if body.requestId:
        try:
            supabase.table("mentor_requests").update({
                "status": "booked",
                "updated_at": datetime.utcnow().isoformat(),
            }).eq("id", body.requestId).execute()
        except Exception as _req_err:
            from loguru import logger as _log
            _log.warning(f"Could not mark request {body.requestId} as booked: {_req_err}")

    stream_channel_id = None
    # NOTE: Chat channel is NOT created here.
    # The student must explicitly pay via POST /api/chat/channels/{session_id}/pay-coins
    # after booking to unlock chat. This ensures a separate, visible coin transaction.

    return {
        "success": True,
        "sessionId": session_id,
        "streamChannelId": stream_channel_id,
        "newBalance": result["new_balance"],
        "message": result["message"],
    }


@router.post("/book")
async def book_session(body: BookSessionBody, user: User = Depends(require_mentee)):
    """Create session + payment + Razorpay order"""
    if user.role.value not in ("mentee", "admin"):
        raise BadRequestError("Only mentees can book sessions")
    if not body.mentorId or not body.amountInr or body.amountInr <= 0:
        raise BadRequestError("mentorId and amountInr (positive) are required")

    supabase = get_supabase_admin()
    mentor_result = supabase.table("users").select("id, name, avatar_url").eq(
        "id", body.mentorId
    ).eq("is_active", True).single().execute()
    if not mentor_result.data:
        raise NotFoundError("Mentor not found or inactive")
    mentor = mentor_result.data

    session_id = booking_service.create_session(
        body.mentorId, user.id, body.scheduledAt, body.durationMinutes or 60
    )
    payment_id = booking_service.create_payment(
        user.id, session_id, body.amountInr,
        f"Session booking with {mentor['name']}",
        {"type": "session_booking", "mentor_id": body.mentorId},
    )
    amount_paise = int(body.amountInr * 100)
    receipt = f"book_{session_id[:8]}_{int(__import__('time').time() * 1000)}"
    order = booking_service.create_razorpay_order(amount_paise, receipt, {
        "session_id": session_id,
        "payment_id": payment_id,
        "type": "session_booking",
    })
    supabase.table("payments").update({"razorpay_order_id": order["id"]}).eq("id", payment_id).execute()

    return {
        "success": True,
        "sessionId": session_id,
        "orderId": order["id"],
        "keyId": settings.razorpay_key_id,
        "amount": order["amount"],
        "currency": order["currency"],
        "paymentId": payment_id,
    }


class VerifyBookBody(BaseModel):
    sessionId: str
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
    paymentId: str


@router.post("/book/verify")
async def verify_session_booking(body: VerifyBookBody, user: User = Depends(require_mentee)):
    """Verify payment and unlock chat"""
    if not settings.razorpay_key_secret:
        raise BadRequestError("Razorpay not configured")
    msg = f"{body.razorpay_order_id}|{body.razorpay_payment_id}"
    expected = hmac.new(
        settings.razorpay_key_secret.encode(), msg.encode(), hashlib.sha256
    ).hexdigest()
    if expected != body.razorpay_signature:
        raise BadRequestError("Payment verification failed")

    supabase = get_supabase_admin()
    from datetime import datetime
    supabase.table("payments").update({
        "razorpay_payment_id": body.razorpay_payment_id,
        "razorpay_signature": body.razorpay_signature,
        "status": "paid",
        "paid_at": datetime.now().isoformat(),
    }).eq("id", body.paymentId).eq("session_id", body.sessionId).execute()

    # Fetch payment to get amount, then credit mentor's wallet (70% of fee)
    payment_result = supabase.table("payments").select("amount").eq(
        "id", body.paymentId
    ).single().execute()
    amount_inr = float(payment_result.data["amount"]) if payment_result.data else 0
    if amount_inr > 0:
        session_result_for_credit = supabase.table("sessions").select(
            "mentor_id, mentee_id"
        ).eq("id", body.sessionId).single().execute()
        if session_result_for_credit.data:
            credit_mentor_for_session_payment(
                mentor_id=session_result_for_credit.data["mentor_id"],
                session_id=body.sessionId,
                amount_inr=amount_inr,
                mentee_id=session_result_for_credit.data["mentee_id"],
                description="Session earning (Razorpay payment)",
            )

    session_result = supabase.table("sessions").select(
        "id, mentor_id, mentee_id, mentor:users!mentor_id(id, name, avatar_url), mentee:users!mentee_id(id, name, avatar_url)"
    ).eq("id", body.sessionId).single().execute()
    if not session_result.data:
        raise NotFoundError("Session not found")
    session = session_result.data
    if session["mentee_id"] != user.id:
        raise BadRequestError("Not authorized to verify this booking")

    mentor = session.get("mentor")
    mentee = session.get("mentee")
    mentor_data = mentor[0] if isinstance(mentor, list) else mentor or {}
    mentee_data = mentee[0] if isinstance(mentee, list) else mentee or {}

    # NOTE: Chat channel is NOT created here.
    # The student must explicitly pay via POST /api/chat/channels/{session_id}/pay-coins
    # after booking to unlock chat.

    return {
        "success": True,
        "message": "Session booked successfully",
        "sessionId": body.sessionId,
        "streamChannelId": None,
    }


@router.post("", response_model=ApiResponse)
async def create_session(
    request: CreateSessionRequest,
    user: User = Depends(get_current_user)
):
    """Create a new session"""
    # If user is mentee, they can only create sessions for themselves
    if user.role.value == "mentee":
        request.mentee_id = user.id
    
    session = await session_service.create_session(request)
    
    return {
        "success": True,
        "data": session,
        "message": "Session created successfully",
    }


@router.get("/{session_id}", response_model=ApiResponse)
async def get_session(
    session_id: str,
    user: User = Depends(get_current_user)
):
    """Get session by ID"""
    session = await session_service.get_session_by_id(session_id)
    
    return {
        "success": True,
        "data": session,
    }


@router.get("", response_model=PaginatedResponse)
async def get_my_sessions(
    status: Optional[str] = Query(None, description="Comma-separated status values"),
    role: str = Query("both", description="Filter by role: mentor, mentee, or both"),
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user)
):
    """Get user's sessions"""
    status_list = None
    if status:
        status_list = [SessionStatus(s.strip()) for s in status.split(",")]
    
    filters = SessionFilters(
        status=status_list,
        role=role,
        from_date=from_date,
        to_date=to_date,
        page=page,
        limit=limit,
    )
    
    result = await session_service.get_user_sessions(user.id, filters)
    
    total_pages = (result["total"] + limit - 1) // limit
    
    return {
        "success": True,
        "data": result["sessions"],
        "pagination": PaginationInfo(
            page=page,
            limit=limit,
            total=result["total"],
            total_pages=total_pages,
        ),
    }


@router.patch("/{session_id}/status", response_model=ApiResponse)
async def update_session_status(
    session_id: str,
    request: UpdateSessionStatus,
    user: User = Depends(get_current_user)
):
    """Update session status"""
    session = await session_service.update_session_status(
        session_id, request.status, user.id
    )
    
    return {
        "success": True,
        "data": session,
        "message": f"Session status updated to {request.status.value}",
    }


@router.post("/{session_id}/cancel", response_model=ApiResponse)
async def cancel_session(
    session_id: str,
    reason: Optional[str] = None,
    user: User = Depends(get_current_user)
):
    """Cancel a session"""
    session = await session_service.cancel_session(session_id, user.id, reason)
    
    return {
        "success": True,
        "data": session,
        "message": "Session cancelled successfully",
    }


@router.post("/{session_id}/reschedule", response_model=ApiResponse)
async def reschedule_session(
    session_id: str,
    request: RescheduleSession,
    user: User = Depends(get_current_user)
):
    """Reschedule a session"""
    session = await session_service.reschedule_session(
        session_id, request.new_time, user.id
    )
    
    return {
        "success": True,
        "data": session,
        "message": "Session rescheduled successfully",
    }


@router.post("/{session_id}/notes", response_model=ApiResponse)
async def add_notes(
    session_id: str,
    request: AddSessionNotes,
    user: User = Depends(get_current_user)
):
    """Add notes to a session"""
    is_mentor = user.role.value in ["mentor", "admin"]
    session = await session_service.add_session_notes(
        session_id, user.id, request.notes, is_mentor
    )
    
    return {
        "success": True,
        "data": session,
        "message": "Notes added successfully",
    }


# =====================================================
# SCHEDULE SESSION WITH JITSI (replaces Zoom endpoint)
# =====================================================

class ScheduleSessionBody(BaseModel):
    request_id: str
    start_time: str  # ISO 8601
    duration_minutes: int = 60
    notes: Optional[str] = None


@router.post("/schedule", response_model=ApiResponse)
async def schedule_session(
    body: ScheduleSessionBody,
    user: User = Depends(get_current_user),
):
    """
    Mentor schedules a session for an accepted request.
    Generates a Jitsi Meet link automatically — no Zoom account needed.
    """
    if user.role.value != "mentor":
        raise BadRequestError("Only mentors can schedule sessions")

    supabase = get_supabase_admin()

    req_res = supabase.table("mentor_requests").select(
        "id, status, mentee_id, accepted_by, topic, title"
    ).eq("id", body.request_id).single().execute()

    if not req_res.data:
        raise NotFoundError("Request not found")

    req = req_res.data
    if req["accepted_by"] != user.id:
        raise BadRequestError("You are not the accepted mentor for this request")
    if req["status"] not in ("accepted", "paid"):
        raise BadRequestError(
            f"Request must be accepted before scheduling (current: {req['status']})"
        )

    # Reuse the placeholder session created on mentor-accept (if it exists)
    existing_sess = supabase.table("sessions").select("id").eq(
        "request_id", body.request_id
    ).execute()

    import uuid as _uuid
    if existing_sess.data:
        session_id = existing_sess.data[0]["id"]
        meeting_url = generate_meeting_url(session_id)
        update_payload: dict = {
            "scheduled_at": body.start_time,
            "duration_minutes": body.duration_minutes,
            "status": "scheduled",
            "meeting_url": meeting_url,
        }
        if body.notes:
            update_payload["mentor_notes"] = body.notes
        sess_res = supabase.table("sessions").update(update_payload).eq("id", session_id).execute()
    else:
        session_id = str(_uuid.uuid4())
        meeting_url = generate_meeting_url(session_id)
        session_insert: dict = {
            "id": session_id,
            "mentor_id": user.id,
            "mentee_id": req["mentee_id"],
            "request_id": body.request_id,
            "scheduled_at": body.start_time,
            "duration_minutes": body.duration_minutes,
            "status": "scheduled",
            "meeting_url": meeting_url,
        }
        if body.notes:
            session_insert["mentor_notes"] = body.notes
        sess_res = supabase.table("sessions").insert(session_insert).execute()

    if not sess_res.data:
        raise BadRequestError("Failed to update/create session record")

    session = sess_res.data[0]

    # Notify mentee
    mentor_res = supabase.table("users").select("name").eq("id", user.id).single().execute()
    mentor_name = (mentor_res.data or {}).get("name", "Your mentor")
    try:
        supabase.table("notifications").insert({
            "user_id": req["mentee_id"],
            "type": "session",
            "title": "Session Scheduled! 🎉",
            "message": (
                f"{mentor_name} has scheduled your session on "
                f"{body.start_time[:10]}. A Jitsi Meet link is ready."
            ),
            "related_entity_type": "session",
            "related_entity_id": session["id"],
            "action_url": "/sessions",
        }).execute()
    except Exception as notif_err:
        from loguru import logger as _log
        _log.warning(f"Notification insert failed: {notif_err}")

    return {
        "success": True,
        "message": "Session scheduled with Jitsi Meet link",
        "data": {
            "session_id": session["id"],
            "meeting_url": meeting_url,
            "scheduled_at": body.start_time,
        },
    }


@router.get("/upcoming/all", response_model=ApiResponse)
async def get_upcoming_sessions(
    hours: int = Query(24, ge=1, le=168),
    user: User = Depends(get_current_user)
):
    """Get upcoming sessions within specified hours"""
    sessions = await session_service.get_upcoming_sessions(hours)
    
    return {
        "success": True,
        "data": sessions,
    }
