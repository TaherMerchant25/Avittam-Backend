# =====================================================
# SESSION ROUTES
# HTTP endpoints for session management
# =====================================================

from fastapi import APIRouter, Depends, Query
from typing import Optional, List
from datetime import datetime

from app.middleware.auth import get_current_user
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


router = APIRouter()


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
