# =====================================================
# NOTIFICATIONS ROUTES
# HTTP endpoints for notification management
# =====================================================

from fastapi import APIRouter, Depends, Query
from typing import Optional

from app.middleware.auth import get_current_user, require_admin
from app.models.schemas import (
    User,
    NotificationType,
    NotificationFilters,
    SendSystemNotification,
    ApiResponse,
    PaginatedResponse,
    PaginationInfo,
)
from app.services import notifications as notification_service
from app.middleware.error_handler import BadRequestError


router = APIRouter()


@router.get("", response_model=PaginatedResponse)
async def get_notifications(
    unread_only: bool = False,
    type: Optional[NotificationType] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user)
):
    """Get user's notifications"""
    filters = NotificationFilters(
        unread_only=unread_only,
        type=type,
        page=page,
        limit=limit,
    )
    
    result = await notification_service.get_user_notifications(user.id, filters)
    
    total_pages = (result["total"] + limit - 1) // limit
    
    return {
        "success": True,
        "data": result["notifications"],
        "unread_count": result["unread_count"],
        "pagination": PaginationInfo(
            page=page,
            limit=limit,
            total=result["total"],
            total_pages=total_pages,
        ),
    }


@router.get("/{notification_id}", response_model=ApiResponse)
async def get_notification(
    notification_id: str,
    user: User = Depends(get_current_user)
):
    """Get single notification"""
    notification = await notification_service.get_notification_by_id(notification_id, user.id)
    
    return {
        "success": True,
        "data": notification,
    }


@router.post("/{notification_id}/read", response_model=ApiResponse)
async def mark_as_read(
    notification_id: str,
    user: User = Depends(get_current_user)
):
    """Mark notification as read"""
    notification = await notification_service.mark_notification_as_read(notification_id, user.id)
    
    return {
        "success": True,
        "data": notification,
    }


@router.post("/read-all", response_model=ApiResponse)
async def mark_all_as_read(
    user: User = Depends(get_current_user)
):
    """Mark all notifications as read"""
    count = await notification_service.mark_all_notifications_as_read(user.id)
    
    return {
        "success": True,
        "marked_count": count,
        "message": f"{count} notifications marked as read",
    }


@router.delete("/{notification_id}", response_model=ApiResponse)
async def delete_notification(
    notification_id: str,
    user: User = Depends(get_current_user)
):
    """Delete a notification"""
    await notification_service.delete_notification(notification_id, user.id)
    
    return {
        "success": True,
        "message": "Notification deleted",
    }


@router.delete("/old/cleanup", response_model=ApiResponse)
async def delete_old_notifications(
    days: int = Query(30, ge=1, le=365),
    user: User = Depends(get_current_user)
):
    """Delete old notifications"""
    count = await notification_service.delete_old_notifications(user.id, days)
    
    return {
        "success": True,
        "deleted_count": count,
        "message": f"{count} old notifications deleted",
    }


@router.post("/system", response_model=ApiResponse)
async def send_system_notification(
    request: SendSystemNotification,
    user: User = Depends(require_admin)
):
    """Send system notification (admin only)"""
    if not request.title or not request.message:
        raise BadRequestError("Title and message are required")
    
    result = await notification_service.send_system_notification(
        request.title,
        request.message,
        request.target_role
    )
    
    return {
        "success": True,
        "sent": result["success"],
        "failed": result["failed"],
        "message": f"Notification sent to {result['success']} users",
    }
