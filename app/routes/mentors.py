# =====================================================
# MENTOR ROUTES
# HTTP endpoints for mentor/ping management
# =====================================================

from fastapi import APIRouter, Depends, Query
from typing import Optional, List

from app.middleware.auth import get_current_user, require_mentor, require_mentee
from app.models.schemas import (
    User,
    CreateMentorRequestInput,
    BroadcastPingInput,
    RequestStatus,
    MentorshipType,
    RequestFilters,
    MentorFilters,
    ApiResponse,
    PaginatedResponse,
    PaginationInfo,
)
from app.services import mentors as mentor_service
from app.middleware.error_handler import BadRequestError


router = APIRouter()


# =====================================================
# MENTOR REQUEST (PING) ENDPOINTS
# =====================================================

@router.post("/requests", response_model=ApiResponse)
async def create_request(
    request: CreateMentorRequestInput,
    user: User = Depends(get_current_user)
):
    """Create a new mentor request (ping)"""
    if not request.title or not request.topic:
        raise BadRequestError("Title and topic are required")
    
    mentor_request = await mentor_service.create_mentor_request(user.id, request)
    
    return {
        "success": True,
        "data": mentor_request,
        "message": "Mentor request created successfully",
    }


@router.post("/requests/broadcast", response_model=ApiResponse)
async def broadcast_ping(
    request: BroadcastPingInput,
    user: User = Depends(get_current_user)
):
    """Broadcast a ping to mentors"""
    if not request.title or not request.topic:
        raise BadRequestError("Title and topic are required")
    
    result = await mentor_service.broadcast_ping(user.id, request)
    
    return {
        "success": True,
        "data": result["request"],
        "notified_mentors": result["notified_mentors"],
        "message": f"Request created and {result['notified_mentors']} mentors notified",
    }


@router.get("/requests/pending", response_model=PaginatedResponse)
async def get_pending_requests(
    topic: Optional[str] = None,
    mentorship_type: Optional[MentorshipType] = None,
    min_bounty: Optional[float] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(require_mentor)
):
    """Get all pending requests for mentors to browse"""
    filters = RequestFilters(
        topic=topic,
        mentorship_type=mentorship_type,
        min_bounty=min_bounty,
        page=page,
        limit=limit,
    )
    
    result = await mentor_service.get_pending_requests(filters)
    
    total_pages = (result["total"] + limit - 1) // limit
    
    return {
        "success": True,
        "data": result["requests"],
        "pagination": PaginationInfo(
            page=page,
            limit=limit,
            total=result["total"],
            total_pages=total_pages,
        ),
    }


@router.post("/requests/{request_id}/lock", response_model=ApiResponse)
async def lock_request(
    request_id: str,
    user: User = Depends(require_mentor)
):
    """Lock a request"""
    request = await mentor_service.lock_request(request_id, user.id)
    
    return {
        "success": True,
        "data": request,
        "message": "Request locked successfully. You have 15 minutes to accept.",
    }


@router.post("/requests/{request_id}/unlock", response_model=ApiResponse)
async def unlock_request(
    request_id: str,
    user: User = Depends(require_mentor)
):
    """Unlock a request"""
    request = await mentor_service.unlock_request(request_id, user.id)
    
    return {
        "success": True,
        "data": request,
        "message": "Request unlocked successfully",
    }


@router.post("/requests/{request_id}/accept", response_model=ApiResponse)
async def accept_request(
    request_id: str,
    user: User = Depends(require_mentor)
):
    """Accept a request"""
    request = await mentor_service.accept_request(request_id, user.id)
    
    return {
        "success": True,
        "data": request,
        "message": "Request accepted! You can now schedule a session with the mentee.",
    }


@router.post("/requests/{request_id}/cancel", response_model=ApiResponse)
async def cancel_request(
    request_id: str,
    user: User = Depends(get_current_user)
):
    """Cancel a request (mentee only)"""
    request = await mentor_service.cancel_request(request_id, user.id)
    
    return {
        "success": True,
        "data": request,
        "message": "Request cancelled successfully",
    }


@router.get("/requests/my", response_model=ApiResponse)
async def get_my_requests(
    status: Optional[str] = Query(None, description="Comma-separated status values"),
    user: User = Depends(get_current_user)
):
    """Get mentee's own requests"""
    status_list = None
    if status:
        status_list = [RequestStatus(s.strip()) for s in status.split(",")]
    
    requests = await mentor_service.get_mentee_requests(user.id, status_list)
    
    return {
        "success": True,
        "data": requests,
    }


@router.post("/requests/{request_id}/view", response_model=ApiResponse)
async def record_view(
    request_id: str,
    user: User = Depends(require_mentor)
):
    """Record that mentor viewed a request"""
    await mentor_service.record_request_view(request_id, user.id)
    
    return {
        "success": True,
        "message": "View recorded",
    }


# =====================================================
# MENTOR DISCOVERY ENDPOINTS
# =====================================================

@router.get("", response_model=PaginatedResponse)
async def get_active_mentors(
    expertise: Optional[str] = Query(None, description="Comma-separated expertise areas"),
    min_rating: Optional[float] = Query(None, ge=0, le=5),
    max_hourly_rate: Optional[float] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user)
):
    """Get all active mentors"""
    expertise_list = None
    if expertise:
        expertise_list = [e.strip() for e in expertise.split(",")]
    
    filters = MentorFilters(
        expertise=expertise_list,
        min_rating=min_rating,
        max_hourly_rate=max_hourly_rate,
        page=page,
        limit=limit,
    )
    
    result = await mentor_service.get_active_mentors(filters)
    
    total_pages = (result["total"] + limit - 1) // limit
    
    return {
        "success": True,
        "data": result["mentors"],
        "pagination": PaginationInfo(
            page=page,
            limit=limit,
            total=result["total"],
            total_pages=total_pages,
        ),
    }


@router.get("/{mentor_id}", response_model=ApiResponse)
async def get_mentor(
    mentor_id: str,
    user: User = Depends(get_current_user)
):
    """Get mentor details by ID"""
    mentor = await mentor_service.get_mentor_by_id(mentor_id)
    
    return {
        "success": True,
        "data": mentor,
    }
