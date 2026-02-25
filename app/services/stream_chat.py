# =====================================================
# STREAM CHAT SERVICE - Server-side Stream Chat Admin
# =====================================================

from typing import Optional
from loguru import logger

from app.config.settings import settings

_stream_client = None


def _get_stream_client():
    """Get or create Stream Chat client (singleton)"""
    global _stream_client
    if not settings.stream_chat_api_key or not settings.stream_chat_api_secret:
        raise ValueError("Stream Chat not configured. Set STREAM_CHAT_API_KEY and STREAM_CHAT_API_SECRET.")
    if _stream_client is None:
        from stream_chat import StreamChat
        _stream_client = StreamChat(
            api_key=settings.stream_chat_api_key,
            api_secret=settings.stream_chat_api_secret,
        )
        logger.info("[StreamChat] Server client initialized")
    return _stream_client


def is_stream_chat_configured() -> bool:
    """Check if Stream Chat is properly configured"""
    return bool(
        settings.stream_chat_api_key and settings.stream_chat_api_secret
        and len(settings.stream_chat_api_key) > 5 and len(settings.stream_chat_api_secret) > 5
    )


def generate_user_token(user_id: str) -> str:
    """Generate a user token for frontend authentication"""
    client = _get_stream_client()
    return client.create_token(user_id)


def upsert_stream_user(
    user_id: str,
    name: str,
    avatar_url: Optional[str] = None,
    role: Optional[str] = None,
) -> None:
    """Upsert a user in Stream Chat"""
    client = _get_stream_client()
    client.upsert_user({
        "id": user_id,
        "name": name or "User",
        "image": avatar_url or "",
        "role": "admin" if role == "mentor" else "user",
    })
    logger.info(f"[StreamChat] Upserted user: {user_id} ({name})")


def create_session_channel(
    session_id: str,
    mentor_id: str,
    mentee_id: str,
    session_topic: Optional[str] = None,
) -> str:
    """Create a chat channel for a session (initially frozen)"""
    client = _get_stream_client()
    channel_id = f"session-{session_id}"
    channel = client.channel(
        "messaging",
        channel_id,
        {
            "name": session_topic or "Mentorship Session Chat",
            "members": [mentor_id, mentee_id],
            "session_id": session_id,
            "frozen": True,
        },
    )
    channel.create(mentee_id)
    logger.info(f"[StreamChat] Created frozen channel: {channel_id}")
    return channel_id


def activate_channel(channel_id: str) -> None:
    """Activate (unfreeze) a channel after payment is verified"""
    client = _get_stream_client()
    channel = client.channel("messaging", channel_id)
    channel.update({"frozen": False})
    channel.send_message({"text": "🎉 Chat unlocked! You can now message each other."}, "system")
    logger.info(f"[StreamChat] Activated channel: {channel_id}")


def ensure_system_user() -> None:
    """Ensure a 'system' user exists for system messages"""
    try:
        client = _get_stream_client()
        client.upsert_user({"id": "system", "name": "MentorGold", "role": "admin"})
    except Exception as e:
        logger.warning(f"[StreamChat] Could not create system user: {e}")
