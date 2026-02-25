# =====================================================
# SESSION BOOKING SERVICE - Create session + payment + chat
# =====================================================

from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import httpx
from loguru import logger

from app.config.database import get_supabase_admin
from app.config.settings import settings
from app.services import stream_chat as stream_chat_service


def create_session(
    mentor_id: str,
    mentee_id: str,
    scheduled_at: Optional[str] = None,
    duration_minutes: int = 60,
) -> str:
    """Create a new session in the database"""
    supabase = get_supabase_admin()
    scheduled = scheduled_at or (datetime.now() + timedelta(days=1)).isoformat()

    result = supabase.table("sessions").insert({
        "mentor_id": mentor_id,
        "mentee_id": mentee_id,
        "scheduled_at": scheduled,
        "duration_minutes": duration_minutes,
        "status": "scheduled",
    }).execute()

    if not result.data:
        raise Exception("Failed to create session")
    return result.data[0]["id"]


def create_payment(
    user_id: str,
    session_id: str,
    amount_inr: float,
    description: str,
    metadata: Optional[Dict] = None,
) -> str:
    """Create a payment record in Supabase"""
    supabase = get_supabase_admin()
    result = supabase.table("payments").insert({
        "user_id": user_id,
        "session_id": session_id,
        "amount": amount_inr,
        "currency": "INR",
        "status": "pending",
        "description": description,
        "metadata": metadata or {},
    }).execute()

    if not result.data:
        raise Exception("Failed to create payment")
    return result.data[0]["id"]


def create_razorpay_order(
    amount_paise: int,
    receipt: str,
    notes: Dict[str, str],
) -> Dict[str, Any]:
    """Create Razorpay order and return order details"""
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise ValueError("Razorpay not configured")

    import base64
    auth = base64.b64encode(
        f"{settings.razorpay_key_id}:{settings.razorpay_key_secret}".encode()
    ).decode()

    with httpx.Client() as client:
        response = client.post(
            "https://api.razorpay.com/v1/orders",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Basic {auth}",
            },
            json={
                "amount": amount_paise,
                "currency": "INR",
                "receipt": receipt,
                "notes": notes,
            },
        )

    if response.status_code != 200:
        data = response.json()
        raise Exception(data.get("error", {}).get("description", "Failed to create Razorpay order"))

    return response.json()


def create_chat_channel_for_session(
    session_id: str,
    mentor_id: str,
    mentee_id: str,
    payment_id: Optional[str],
    mentor_name: str,
    mentee_name: str,
    mentor_avatar: Optional[str],
    mentee_avatar: Optional[str],
    topic: str = "Mentorship Session Chat",
) -> str:
    """Create chat channel and Stream channel for a session after payment verification"""
    supabase = get_supabase_admin()

    if not stream_chat_service.is_stream_chat_configured():
        raise ValueError("Stream Chat not configured")

    stream_chat_service.ensure_system_user()
    stream_chat_service.upsert_stream_user(mentor_id, mentor_name, mentor_avatar, "mentor")
    stream_chat_service.upsert_stream_user(mentee_id, mentee_name, mentee_avatar, "mentee")

    stream_channel_id = stream_chat_service.create_session_channel(
        session_id, mentor_id, mentee_id, topic
    )
    stream_chat_service.activate_channel(stream_channel_id)

    insert_data = {
        "session_id": session_id,
        "mentor_id": mentor_id,
        "mentee_id": mentee_id,
        "stream_channel_id": stream_channel_id,
        "is_active": True,
    }
    if payment_id:
        insert_data["payment_id"] = payment_id
    supabase.table("chat_channels").insert(insert_data).execute()

    return stream_channel_id
