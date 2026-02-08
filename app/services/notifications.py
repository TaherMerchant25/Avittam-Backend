# =====================================================
# NOTIFICATION SERVICE
# Create and manage user notifications
# =====================================================

from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from loguru import logger

from app.config.database import get_supabase_admin
from app.models.schemas import NotificationType, NotificationFilters
from app.middleware.error_handler import NotFoundError


async def create_notification(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Create a single notification"""
    supabase = get_supabase_admin()
    
    notification_data = {
        "user_id": input_data["user_id"],
        "type": input_data["type"],
        "title": input_data["title"],
        "message": input_data["message"],
        "related_entity_type": input_data.get("related_entity_type"),
        "related_entity_id": input_data.get("related_entity_id"),
        "action_url": input_data.get("action_url"),
        "is_read": False,
    }
    
    result = supabase.table("notifications").insert(notification_data).execute()
    
    if not result.data:
        logger.error("Failed to create notification")
        raise Exception("Failed to create notification")
    
    return result.data[0]


async def broadcast_notifications(
    user_ids: List[str],
    notification_data: Dict[str, Any]
) -> Dict[str, int]:
    """Broadcast notifications to multiple users"""
    supabase = get_supabase_admin()
    
    notifications = [
        {
            "user_id": user_id,
            "type": notification_data["type"],
            "title": notification_data["title"],
            "message": notification_data["message"],
            "related_entity_type": notification_data.get("related_entity_type"),
            "related_entity_id": notification_data.get("related_entity_id"),
            "action_url": notification_data.get("action_url"),
            "is_read": False,
        }
        for user_id in user_ids
    ]
    
    result = supabase.table("notifications").insert(notifications).execute()
    
    if not result.data:
        logger.error("Failed to broadcast notifications")
        return {"success": 0, "failed": len(user_ids)}
    
    return {
        "success": len(result.data),
        "failed": len(user_ids) - len(result.data),
    }


async def get_user_notifications(
    user_id: str,
    filters: NotificationFilters
) -> Dict[str, Any]:
    """Get notifications for a user"""
    supabase = get_supabase_admin()
    
    query = supabase.table("notifications").select("*", count="exact").eq("user_id", user_id)
    
    if filters.unread_only:
        query = query.eq("is_read", False)
    
    if filters.type:
        query = query.eq("type", filters.type.value)
    
    offset = (filters.page - 1) * filters.limit
    query = query.order("created_at", desc=True).range(offset, offset + filters.limit - 1)
    
    result = query.execute()
    
    # Get unread count
    unread_result = supabase.table("notifications").select(
        "*", count="exact", head=True
    ).eq("user_id", user_id).eq("is_read", False).execute()
    
    return {
        "notifications": result.data or [],
        "total": result.count or 0,
        "unread_count": unread_result.count or 0,
    }


async def get_notification_by_id(
    notification_id: str,
    user_id: str
) -> Dict[str, Any]:
    """Get a single notification"""
    supabase = get_supabase_admin()
    
    result = supabase.table("notifications").select("*").eq(
        "id", notification_id
    ).eq("user_id", user_id).single().execute()
    
    if not result.data:
        raise NotFoundError("Notification not found")
    
    return result.data


async def mark_notification_as_read(
    notification_id: str,
    user_id: str
) -> Dict[str, Any]:
    """Mark notification as read"""
    supabase = get_supabase_admin()
    
    result = supabase.table("notifications").update({
        "is_read": True,
        "read_at": datetime.now().isoformat(),
    }).eq("id", notification_id).eq("user_id", user_id).execute()
    
    if not result.data:
        raise NotFoundError("Notification not found")
    
    return result.data[0]


async def mark_all_notifications_as_read(user_id: str) -> int:
    """Mark all notifications as read for a user"""
    supabase = get_supabase_admin()
    
    result = supabase.table("notifications").update({
        "is_read": True,
        "read_at": datetime.now().isoformat(),
    }).eq("user_id", user_id).eq("is_read", False).execute()
    
    return len(result.data) if result.data else 0


async def delete_notification(notification_id: str, user_id: str) -> None:
    """Delete a notification"""
    supabase = get_supabase_admin()
    
    supabase.table("notifications").delete().eq(
        "id", notification_id
    ).eq("user_id", user_id).execute()


async def delete_old_notifications(user_id: str, older_than_days: int = 30) -> int:
    """Delete old notifications"""
    supabase = get_supabase_admin()
    
    cutoff_date = datetime.now() - timedelta(days=older_than_days)
    
    result = supabase.table("notifications").delete().eq(
        "user_id", user_id
    ).lt("created_at", cutoff_date.isoformat()).execute()
    
    return len(result.data) if result.data else 0


async def send_system_notification(
    title: str,
    message: str,
    target_role: str = "all"
) -> Dict[str, int]:
    """Send system notification to users by role"""
    supabase = get_supabase_admin()
    
    # Get target users
    query = supabase.table("users").select("id").eq("is_active", True)
    
    if target_role != "all":
        query = query.eq("role", target_role)
    
    users_result = query.execute()
    
    if not users_result.data:
        return {"success": 0, "failed": 0}
    
    user_ids = [u["id"] for u in users_result.data]
    
    return await broadcast_notifications(
        user_ids,
        {
            "type": NotificationType.SYSTEM.value,
            "title": title,
            "message": message,
        }
    )
