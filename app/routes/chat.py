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
    Also returns coin_cost so the frontend can show the price upfront.
    Does NOT auto-create — chat is only created after student pays via /pay-coins.
    """
    supabase = get_supabase_admin()

    # Determine coin cost from the session's associated request bounty
    coin_cost = 500.0
    try:
        sess = supabase.table("sessions").select("request_id").eq("id", session_id).execute()
        if sess.data and sess.data[0].get("request_id"):
            req = supabase.table("mentor_requests").select("bounty").eq(
                "id", sess.data[0]["request_id"]
            ).execute()
            if req.data and req.data[0].get("bounty"):
                coin_cost = float(req.data[0]["bounty"])
    except Exception:
        pass

    result = supabase.table("chat_channels").select("*").eq("session_id", session_id).execute()
    channel = result.data[0] if result.data else None

    if not channel:
        return {
            "success": True,
            "chatChannel": None,
            "isActive": False,
            "coin_cost": coin_cost,
        }

    return {
        "success": True,
        "chatChannel": channel,
        "isActive": channel.get("is_active", False),
        "coin_cost": coin_cost,
    }


# =====================================================
# POST /api/chat/session/{session_id}/create-channel
# =====================================================
@router.post("/session/{session_id}/create-channel")
async def create_session_chat_channel(session_id: str, user: User = Depends(get_current_user)):
    """
    Create or restore a chat channel for a booked session.
    RESTRICTED TO MENTORS ONLY — mentees must unlock chat via
    POST /api/chat/channels/{session_id}/pay-coins to prevent bypassing payment.
    """
    supabase = get_supabase_admin()

    # Verify session exists and user is a participant
    session_result = supabase.table("sessions").select("*").eq("id", session_id).execute()
    if not session_result.data:
        raise NotFoundError("Session not found")
    session = session_result.data[0]

    # Only the mentor of this session may use this endpoint
    if session["mentor_id"] != user.id:
        raise BadRequestError("Only the mentor of this session can restore a chat channel via this endpoint.")

    # If channel already exists just return it (activate if inactive)
    existing = supabase.table("chat_channels").select("*").eq("session_id", session_id).execute()
    if existing.data:
        channel = existing.data[0]
        if not channel.get("is_active"):
            supabase.table("chat_channels").update({"is_active": True}).eq("id", channel["id"]).execute()
            channel["is_active"] = True
            try:
                from app.services import stream_chat as scs
                if scs.is_stream_chat_configured():
                    scs.activate_channel(channel["stream_channel_id"])
            except Exception as act_err:
                logger.warning(f"Could not activate stream channel: {act_err}")
        return {"success": True, "chatChannel": channel, "isActive": True}

    # Create a fresh channel via the booking service
    mentor_id = session["mentor_id"]
    mentee_id = session["mentee_id"]

    mentor_result = supabase.table("users").select("name, avatar_url").eq("id", mentor_id).single().execute()
    mentee_result = supabase.table("users").select("name, avatar_url").eq("id", mentee_id).single().execute()
    mentor_data = mentor_result.data or {}
    mentee_data = mentee_result.data or {}

    from app.services import session_booking as booking_svc
    stream_channel_id = booking_svc.create_chat_channel_for_session(
        session_id=session_id,
        mentor_id=mentor_id,
        mentee_id=mentee_id,
        payment_id=None,
        mentor_name=mentor_data.get("name", "Mentor"),
        mentee_name=mentee_data.get("name", "Student"),
        mentor_avatar=mentor_data.get("avatar_url"),
        mentee_avatar=mentee_data.get("avatar_url"),
    )

    channel_result = supabase.table("chat_channels").select("*").eq("session_id", session_id).execute()
    channel = channel_result.data[0] if channel_result.data else {
        "session_id": session_id,
        "stream_channel_id": stream_channel_id,
        "is_active": True,
    }

    return {"success": True, "chatChannel": channel, "isActive": True}


# =====================================================
# POST /api/chat/channels/{session_id}/pay-coins
# Mentee pays with Avittam Coins to unlock chat.
# Mentor receives 70% of the cost immediately.
# =====================================================

@router.post("/channels/{session_id}/pay-coins")
async def pay_chat_with_coins(session_id: str, user: User = Depends(get_current_user)):
    """Unlock chat by paying with Avittam Coins. Mentor gets 70%."""
    from fastapi.responses import JSONResponse as _JSONResponse
    from app.services.wallets import get_or_create_wallet

    supabase = get_supabase_admin()

    # ── 1. Fetch session & verify caller is the mentee ──────────────────
    session_result = supabase.table("sessions").select("*").eq("id", session_id).single().execute()
    if not session_result.data:
        raise NotFoundError("Session not found")
    session = session_result.data

    if session["mentee_id"] != user.id:
        raise BadRequestError("Only the mentee can pay to unlock chat")

    mentor_id = session["mentor_id"]
    mentee_id = user.id

    # ── 2. Already unlocked? Return early ──────────────────────────────
    existing = supabase.table("chat_channels").select("*").eq("session_id", session_id).execute()
    if existing.data and existing.data[0].get("is_active"):
        return {
            "success": True,
            "message": "Chat already unlocked",
            "chatChannel": existing.data[0],
            "isActive": True,
            "alreadyPaid": True,
        }

    # ── 3. Determine coin cost from bounty ─────────────────────────────
    coin_cost = 500.0
    if session.get("request_id"):
        req = supabase.table("mentor_requests").select("bounty").eq(
            "id", session["request_id"]
        ).execute()
        if req.data and req.data[0].get("bounty"):
            coin_cost = float(req.data[0]["bounty"])

    mentor_share = round(coin_cost * 0.70, 2)

    # ── 4. Check mentee wallet balance ─────────────────────────────────
    mentee_wallet = get_or_create_wallet(mentee_id, "student")
    mentee_balance = float(mentee_wallet["balance"])

    if mentee_balance < coin_cost:
        return _JSONResponse(
            status_code=402,
            content={
                "success": False,
                "error": (
                    f"Insufficient Avittam Coins. "
                    f"You need {coin_cost:.0f} coins but only have {mentee_balance:.0f}. "
                    f"Please load more coins from your wallet."
                ),
                "coin_cost": coin_cost,
                "current_balance": mentee_balance,
            },
        )

    # ── 5. Debit mentee student wallet ─────────────────────────────────
    new_mentee_balance = mentee_balance - coin_cost
    supabase.table("wallets").update({
        "balance": new_mentee_balance,
        "total_debited": float(mentee_wallet["total_debited"]) + coin_cost,
    }).eq("id", mentee_wallet["id"]).execute()

    supabase.table("wallet_transactions").insert({
        "wallet_id": mentee_wallet["id"],
        "tx_type": "debit",
        "category": "session_payment",
        "amount": coin_cost,
        "balance_after": new_mentee_balance,
        "session_id": session_id,
        "related_user_id": mentor_id,
        "description": f"Chat unlock: paid {coin_cost:.0f} Avittam Coins",
    }).execute()

    # ── 6. Credit mentor mentorship wallet (70%) ───────────────────────
    mentor_wallet = get_or_create_wallet(mentor_id, "mentorship")
    new_mentor_balance = float(mentor_wallet["balance"]) + mentor_share
    supabase.table("wallets").update({
        "balance": new_mentor_balance,
        "total_credited": float(mentor_wallet["total_credited"]) + mentor_share,
    }).eq("id", mentor_wallet["id"]).execute()

    supabase.table("wallet_transactions").insert({
        "wallet_id": mentor_wallet["id"],
        "tx_type": "credit",
        "category": "session_earning",
        "amount": mentor_share,
        "balance_after": new_mentor_balance,
        "session_id": session_id,
        "related_user_id": mentee_id,
        "description": f"Chat unlock earning: {mentor_share:.0f} coins (70% of {coin_cost:.0f})",
    }).execute()

    # ── 7. Fetch user display data for channel creation ────────────────
    mentor_result = supabase.table("users").select("name, avatar_url").eq(
        "id", mentor_id
    ).single().execute()
    mentee_result = supabase.table("users").select("name, avatar_url").eq(
        "id", mentee_id
    ).single().execute()
    mentor_data = mentor_result.data or {}
    mentee_data = mentee_result.data or {}

    # ── 8. Create / activate Stream Chat channel ───────────────────────
    stream_channel_id: Optional[str] = None
    if existing.data:
        channel_row = existing.data[0]
        stream_channel_id = channel_row["stream_channel_id"]
        try:
            stream_chat_service.activate_channel(stream_channel_id)
        except Exception as _e:
            logger.warning(f"Could not activate stream channel: {_e}")
        supabase.table("chat_channels").update({"is_active": True}).eq(
            "id", channel_row["id"]
        ).execute()
    else:
        topic = "Mentorship Session"
        if session.get("request_id"):
            req2 = supabase.table("mentor_requests").select("title, topic").eq(
                "id", session["request_id"]
            ).execute()
            if req2.data:
                topic = req2.data[0].get("topic") or req2.data[0].get("title") or topic

        stream_chat_service.ensure_system_user()
        stream_chat_service.upsert_stream_user(
            mentor_id, mentor_data.get("name", "Mentor"), mentor_data.get("avatar_url"), "mentor"
        )
        stream_chat_service.upsert_stream_user(
            mentee_id, mentee_data.get("name", "Student"), mentee_data.get("avatar_url"), "mentee"
        )
        stream_channel_id = stream_chat_service.create_session_channel(
            session_id, mentor_id, mentee_id, topic
        )
        stream_chat_service.activate_channel(stream_channel_id)
        supabase.table("chat_channels").insert({
            "session_id": session_id,
            "mentor_id": mentor_id,
            "mentee_id": mentee_id,
            "stream_channel_id": stream_channel_id,
            "is_active": True,
        }).execute()

    # ── 9. Notify mentor ───────────────────────────────────────────────
    try:
        supabase.table("notifications").insert({
            "user_id": mentor_id,
            "type": "chat",
            "title": "Chat Unlocked! 💬",
            "message": (
                f"{mentee_data.get('name', 'Student')} has paid "
                f"{coin_cost:.0f} coins to unlock chat for your session."
            ),
            "related_entity_type": "chat_channel",
            "related_entity_id": session_id,
            "action_url": f"/chat/{stream_channel_id}",
        }).execute()
    except Exception as _ne:
        logger.warning(f"Could not send unlock notification: {_ne}")

    channel_result = supabase.table("chat_channels").select("*").eq(
        "session_id", session_id
    ).execute()
    channel = channel_result.data[0] if channel_result.data else {
        "session_id": session_id,
        "stream_channel_id": stream_channel_id,
        "is_active": True,
    }

    logger.info(
        f"Chat unlocked for session {session_id}: mentee {mentee_id} paid {coin_cost} coins, "
        f"mentor {mentor_id} received {mentor_share} coins."
    )

    return {
        "success": True,
        "message": f"Chat unlocked! {coin_cost:.0f} coins paid, mentor credited {mentor_share:.0f} coins.",
        "chatChannel": channel,
        "isActive": True,
        "coins_charged": coin_cost,
        "mentor_credited": mentor_share,
        "new_balance": new_mentee_balance,
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
