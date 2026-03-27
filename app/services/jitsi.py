# =====================================================
# JITSI MEET SERVICE
# Platform-level video meeting URL generator.
# No OAuth, no per-user accounts — Jitsi Meet is
# completely free and open, so all we need is a
# unique room name derived from the session UUID.
# =====================================================

import re


def generate_meeting_url(session_id: str) -> str:
    """
    Return a stable Jitsi Meet room URL for a given session ID.

    The room name is derived from the session UUID so that calling
    this function multiple times with the same session_id always
    returns the same URL (idempotent).

    Example:
        session_id = "abc12345-..."
        → "https://meet.jit.si/avittam-abc12345"
    """
    safe_id = re.sub(r"[^a-zA-Z0-9-]", "", session_id)[:36]
    room = f"avittam-{safe_id}"
    return f"https://meet.jit.si/{room}"
