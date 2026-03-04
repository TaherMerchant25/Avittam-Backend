# =====================================================
# CHAT ROUTES - Stream Chat token, channels, pay, verify
# =====================================================

import hmac
import hashlib
import json
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from typing import Optional, List, Any

from app.middleware.auth import get_current_user
from app.config.database import get_supabase_admin
from app.config.settings import settings
from app.models.schemas import User
from app.services import stream_chat as stream_chat_service
from app.middleware.error_handler import BadRequestError, NotFoundError
from loguru import logger


router = APIRouter()


# =====================================================
# POST /api/chat/token
# =====================================================
@router.post("/token")
async def get_token(user: User = Depends(get_current_user)):
    """Get Stream Chat user token"""
    if not stream_chat_service.is_stream_chat_configured():
        raise BadRequestError("Stream Chat not configured")
    stream_chat_service.upsert_stream_user(
        user.id, user.name or user.email, user.avatar_url, user.role.value
    )
    token = stream_chat_service.generate_user_token(user.id)
    return {
        "success": True,
        "token": token,
        "apiKey": settings.stream_chat_api_key,
        "user": {"id": user.id, "name": user.name, "avatar_url": user.avatar_url},
    }


# =====================================================
# GET /api/chat/channels
# =====================================================
@router.get("/channels")
async def get_channels(user: User = Depends(get_current_user)):
    """Get user's chat channels"""
    supabase = get_supabase_admin()
    result = supabase.table("chat_channels").select("*").or_(
        f"mentor_id.eq.{user.id},mentee_id.eq.{user.id}"
    ).order("updated_at", desc=True).execute()
    return {"success": True, "channels": result.data or []}


# =====================================================
# GET /api/chat/session/{session_id}
# =====================================================
@router.get("/session/{session_id}")
async def get_session_chat_status(session_id: str, user: User = Depends(get_current_user)):
    """
    Get chat status for a session.
    Returns the existing channel if it exists and is_active.
    Does NOT auto-create — chat is only created after student pays via /book/coins.
    """
    supabase = get_supabase_admin()
    result = supabase.table("chat_channels").select("*").eq("session_id", session_id).execute()
    channel = result.data[0] if result.data else None

    # Only return channel if it was explicitly created (payment confirmed)
    # Never auto-create here — that bypasses the coin payment gate.
    if not channel:
        return {
            "success": True,
            "chatChannel": None,
            "isActive": False,
        }

    return {
        "success": True,
        "chatChannel": channel,
        "isActive": channel.get("is_active", False),
    }


# =====================================================
# POST /api/chat/channels/{session_id}/pay
# =====================================================
class ChatPayResponse(BaseModel):
    pass


@router.post("/channels/{session_id}/pay")
async def create_chat_payment_order(session_id: str, user: User = Depends(get_current_user)):
    """Create Razorpay order for chat unlock"""
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise BadRequestError("Razorpay not configured")

    supabase = get_supabase_admin()

    session_result = supabase.table("sessions").select("*").eq("id", session_id).single().execute()

    if not session_result.data:
        raise NotFoundError("Session not found")
    session = session_result.data

    if session["mentor_id"] != user.id and session["mentee_id"] != user.id:
        raise BadRequestError("Not a participant of this session")

    existing = supabase.table("chat_channels").select("*").eq("session_id", session_id).execute()
    if existing.data and existing.data[0].get("is_active"):
        return {
            "success": True,
            "message": "Chat already unlocked",
            "channel": existing.data[0],
            "alreadyPaid": True,
        }

    bounty = 500
    if session.get("request_id"):
        req = supabase.table("mentor_requests").select("bounty").eq("id", session["request_id"]).execute()
        if req.data:
            bounty = req.data[0].get("bounty") or 500
    amount = int(bounty * 100)

    other_id = session["mentee_id"] if session["mentor_id"] == user.id else session["mentor_id"]
    other = supabase.table("users").select("name").eq("id", other_id).single().execute()
    other_name = other.data.get("name", "participant") if other.data else "participant"
    payment_result = supabase.table("payments").insert({
        "user_id": user.id,
        "session_id": session_id,
        "amount": amount / 100,
        "currency": "INR",
        "status": "pending",
        "description": f"Chat unlock for session with {other_name}",
        "metadata": {"type": "chat_unlock", "session_id": session_id},
    }).execute()

    if not payment_result.data:
        raise BadRequestError("Failed to create payment")
    payment = payment_result.data[0]

    receipt = f"chat_{session_id[:8]}_{int(__import__('time').time() * 1000)}"
    order = create_razorpay_order(amount, receipt, {
        "session_id": session_id,
        "payment_id": payment["id"],
        "type": "chat_unlock",
    })

    supabase.table("payments").update({"razorpay_order_id": order["id"]}).eq("id", payment["id"]).execute()

    return {
        "success": True,
        "order": {"id": order["id"], "amount": order["amount"], "currency": order["currency"]},
        "paymentId": payment["id"],
        "keyId": settings.razorpay_key_id,
    }


# =====================================================
# POST /api/chat/webhook  (called by Stream Chat servers)
# =====================================================

@router.post("/webhook")
async def stream_chat_webhook(request: Request):
    """
    Stream Chat webhook — listens for message.new events and responds to bot commands.

    Commands:
      /meet  — Generates a Jitsi Meet link for the session
      /help  — Lists available commands
    """
    body = await request.body()
    signature = request.headers.get("X-Signature", "")

    # Verify signature (warn only — lets local dev work without the secret)
    if signature and not stream_chat_service.verify_webhook_signature(body, signature):
        logger.warning("[ChatWebhook] X-Signature mismatch — check STREAM_CHAT_API_SECRET")

    try:
        event = json.loads(body)
    except Exception:
        return {"success": False, "error": "Invalid JSON body"}

    # Only handle new messages
    if event.get("type") != "message.new":
        return {"success": True}

    message = event.get("message", {})
    text = (message.get("text") or "").strip()
    sender_id = (message.get("user") or {}).get("id", "")
    channel_id = event.get("channel_id", "")
    channel_type = event.get("channel_type", "messaging")

    # Never reply to ourselves (infinite-loop guard)
    if sender_id == "avittam-bot":
        return {"success": True}

    if not text.startswith("/"):
        return {"success": True}

    cmd = text.lower().split()[0]

    try:
        if cmd == "/meet":
            # Deterministic Jitsi room from channel_id (no API key needed)
            room_hash = hashlib.sha256(channel_id.encode()).hexdigest()[:12]
            meet_url = f"https://meet.jit.si/avittam-{room_hash}"

            # Persist link in sessions.google_meet_link if possible
            try:
                session_id = channel_id.removeprefix("session-")
                supabase = get_supabase_admin()
                supabase.table("sessions").update(
                    {"google_meet_link": meet_url}
                ).eq("id", session_id).execute()
            except Exception as db_err:
                logger.warning(f"[ChatWebhook] Could not persist meet link: {db_err}")

            response_text = (
                f"🎥 **Your meeting link is ready!**\n\n"
                f"🔗 {meet_url}\n\n"
                f"_Powered by Jitsi Meet — no sign-in required. "
                f"Share this link with your session partner._"
            )
            stream_chat_service.ensure_bot_user()
            stream_chat_service.send_bot_message(channel_type, channel_id, response_text)
            logger.info(f"[ChatWebhook] /meet handled for channel {channel_id}")

        elif cmd == "/help":
            help_text = (
                "🤖 **Avittam Bot Commands**\n\n"
                "• `/meet` — Generate a Jitsi video meeting link for this session\n"
                "• `/help` — Show this help message"
            )
            stream_chat_service.ensure_bot_user()
            stream_chat_service.send_bot_message(channel_type, channel_id, help_text)

    except Exception as e:
        logger.error(f"[ChatWebhook] Error handling command '{cmd}': {e}")

    return {"success": True}


def create_razorpay_order(amount: int, receipt: str, notes: dict):
    import base64
    import httpx
    auth = base64.b64encode(
        f"{settings.razorpay_key_id}:{settings.razorpay_key_secret}".encode()
    ).decode()
    with httpx.Client() as client:
        r = client.post(
            "https://api.razorpay.com/v1/orders",
            headers={"Content-Type": "application/json", "Authorization": f"Basic {auth}"},
            json={"amount": amount, "currency": "INR", "receipt": receipt, "notes": notes},
        )
    if r.status_code != 200:
        raise BadRequestError(r.json().get("error", {}).get("description", "Failed to create order"))
    return r.json()


# =====================================================
# POST /api/chat/channels/{session_id}/verify
# =====================================================
class VerifyChatBody(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
    paymentId: str


@router.post("/channels/{session_id}/verify")
async def verify_chat_payment(session_id: str, body: VerifyChatBody, user: User = Depends(get_current_user)):
    """Verify chat payment and unlock channel"""
    if not settings.razorpay_key_secret:
        raise BadRequestError("Razorpay not configured")

    msg = f"{body.razorpay_order_id}|{body.razorpay_payment_id}"
    expected = hmac.new(
        settings.razorpay_key_secret.encode(),
        msg.encode(),
        hashlib.sha256,
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
    }).eq("id", body.paymentId).execute()

    session_result = supabase.table("sessions").select("*").eq("id", session_id).single().execute()
    if not session_result.data:
        raise NotFoundError("Session not found")
    session = session_result.data

    mentor = supabase.table("users").select("name, avatar_url").eq("id", session["mentor_id"]).single().execute()
    mentee = supabase.table("users").select("name, avatar_url").eq("id", session["mentee_id"]).single().execute()
    mentor_data = mentor.data or {}
    mentee_data = mentee.data or {}

    stream_chat_service.ensure_system_user()
    stream_chat_service.upsert_stream_user(
        session["mentor_id"], mentor_data.get("name", "Mentor"), mentor_data.get("avatar_url"), "mentor"
    )
    stream_chat_service.upsert_stream_user(
        session["mentee_id"], mentee_data.get("name", "Student"), mentee_data.get("avatar_url"), "mentee"
    )

    topic = "Mentorship Session"
    if session.get("request_id"):
        req = supabase.table("mentor_requests").select("title, topic").eq("id", session["request_id"]).execute()
        if req.data:
            topic = req.data[0].get("topic") or req.data[0].get("title") or topic

    existing = supabase.table("chat_channels").select("*").eq("session_id", session_id).execute()
    if existing.data:
        stream_channel_id = existing.data[0]["stream_channel_id"]
        stream_chat_service.activate_channel(stream_channel_id)
        supabase.table("chat_channels").update({"is_active": True, "payment_id": body.paymentId}).eq(
            "id", existing.data[0]["id"]
        ).execute()
    else:
        stream_channel_id = stream_chat_service.create_session_channel(
            session_id, session["mentor_id"], session["mentee_id"], topic
        )
        stream_chat_service.activate_channel(stream_channel_id)
        supabase.table("chat_channels").insert({
            "session_id": session_id,
            "mentor_id": session["mentor_id"],
            "mentee_id": session["mentee_id"],
            "stream_channel_id": stream_channel_id,
            "payment_id": body.paymentId,
            "is_active": True,
        }).execute()

    other_id = session["mentee_id"] if user.id == session["mentor_id"] else session["mentor_id"]
    supabase.table("notifications").insert({
        "user_id": other_id,
        "type": "chat",
        "title": "Chat Unlocked! 💬",
        "message": f"{user.name or 'A participant'} has unlocked the chat for your session.",
        "related_entity_type": "chat_channel",
        "related_entity_id": session_id,
        "action_url": f"/chat/{stream_channel_id}",
    }).execute()

    return {
        "success": True,
        "message": "Chat unlocked successfully",
        "channel": {"stream_channel_id": stream_channel_id, "session_id": session_id, "is_active": True},
    }
