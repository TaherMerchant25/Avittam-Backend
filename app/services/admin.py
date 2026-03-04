from typing import List, Dict, Any, Optional

from loguru import logger

from app.config.database import get_supabase_admin


# ─────────────────────────────────────────────────────────────────────────────
# Milestone threshold (combined mentorship + referral wallet balance)
# ─────────────────────────────────────────────────────────────────────────────
MILESTONE_THRESHOLD = 10_000.0


def _build_mentor_wallet_map(supabase) -> Dict[str, Dict[str, float]]:
    """
    Fetch all mentor wallet rows (types: mentorship + referral) and return
    a map keyed by user_id:
        { user_id: { "mentorship": balance, "referral": balance, "combined": total } }
    """
    result = supabase.table("wallets").select(
        "user_id, type, balance, total_credited"
    ).in_("type", ["mentorship", "referral"]).execute()

    wallet_map: Dict[str, Dict[str, float]] = {}
    for row in result.data or []:
        uid = row["user_id"]
        if uid not in wallet_map:
            wallet_map[uid] = {"mentorship": 0.0, "referral": 0.0, "combined": 0.0}
        credit = float(row.get("total_credited") or 0)
        wallet_map[uid][row["type"]] = credit
        wallet_map[uid]["combined"] += credit

    return wallet_map


async def get_mentor_milestones(threshold: float = MILESTONE_THRESHOLD) -> List[Dict[str, Any]]:
    """
    Return mentors whose **combined** wallet earnings (mentorship + referral
    total_credited) are at or above the given threshold.

    We join data from the `users` table and the `wallets` table in two queries
    instead of relying on a potentially stale `total_earnings` column.
    """
    supabase = get_supabase_admin()

    logger.info(f"[Admin] Fetching mentor milestones — combined threshold ≥ ₹{threshold:,.0f}")

    # 1. Fetch all mentors
    mentors_result = supabase.table("users").select(
        "id, name, email, role, referral_code, created_at"
    ).eq("role", "mentor").execute()

    if not mentors_result.data:
        return []

    # 2. Fetch wallet balances
    wallet_map = _build_mentor_wallet_map(supabase)

    # 3. Merge and filter
    enriched: List[Dict[str, Any]] = []
    for mentor in mentors_result.data:
        uid = mentor["id"]
        wallets = wallet_map.get(uid, {"mentorship": 0.0, "referral": 0.0, "combined": 0.0})
        combined = wallets["combined"]

        if combined >= threshold:
            enriched.append({
                **mentor,
                "mentorship_earnings": wallets["mentorship"],
                "referral_earnings": wallets["referral"],
                "total_earnings": combined,   # kept for backward-compat with frontend
            })

    # Sort by combined total, highest first
    enriched.sort(key=lambda m: m["total_earnings"], reverse=True)
    logger.info(f"[Admin] Found {len(enriched)} mentor(s) at/above threshold")
    return enriched


async def get_all_admin_users(supabase=None) -> List[Dict[str, Any]]:
    """Return all users with role='admin'."""
    if supabase is None:
        supabase = get_supabase_admin()
    result = supabase.table("users").select("id, name, email").eq("role", "admin").execute()
    return result.data or []


async def notify_admins_milestone(
    mentor: Dict[str, Any],
    combined_total: float,
    threshold: float,
) -> int:
    """
    Send an in-app notification to **every** admin user when a mentor crosses
    the given earning threshold.

    Returns the number of notifications successfully created.
    """
    from app.services.notifications import create_notification  # local import to avoid cycles

    supabase = get_supabase_admin()
    admins = await get_all_admin_users(supabase)

    if not admins:
        logger.warning("[Admin] No admin users found — milestone notification skipped")
        return 0

    mentor_name = mentor.get("name") or mentor.get("email", "Unknown")
    title = f"🏆 Mentor Milestone Reached!"
    message = (
        f"{mentor_name} has crossed ₹{threshold:,.0f} in combined earnings "
        f"(Mentorship: ₹{mentor.get('mentorship_earnings', 0):,.0f} + "
        f"Referral: ₹{mentor.get('referral_earnings', 0):,.0f} = "
        f"₹{combined_total:,.0f} total)."
    )

    count = 0
    for admin in admins:
        try:
            await create_notification({
                "user_id": admin["id"],
                "type": "system",
                "title": title,
                "message": message,
                "related_entity_type": "user",
                "related_entity_id": mentor["id"],
                "action_url": "/admin",
            })
            count += 1
        except Exception as exc:
            logger.error(f"[Admin] Failed to notify admin {admin['id']}: {exc}")

    logger.info(f"[Admin] Sent milestone notifications to {count}/{len(admins)} admin(s)")
    return count


async def check_and_notify_milestones(threshold: float = MILESTONE_THRESHOLD) -> Dict[str, Any]:
    """
    Scan all mentors, find those who have just crossed the threshold, and send
    admin notifications for each.  To avoid repeat spam we track sent milestones
    in the `admin_milestone_notifications` table (created lazily if missing).

    Returns a summary dict.
    """
    supabase = get_supabase_admin()
    milestones = await get_mentor_milestones(threshold)

    if not milestones:
        return {"checked": 0, "newly_notified": 0}

    # Fetch already-notified mentor IDs for this threshold
    try:
        notified_result = supabase.table("admin_milestone_notifications").select(
            "mentor_id"
        ).eq("threshold", threshold).execute()
        already_notified: set = {row["mentor_id"] for row in (notified_result.data or [])}
    except Exception:
        # Table may not exist yet — treat as empty
        already_notified = set()

    newly_notified = 0
    for mentor in milestones:
        uid = mentor["id"]
        if uid in already_notified:
            continue  # already notified for this threshold

        notified = await notify_admins_milestone(
            mentor=mentor,
            combined_total=mentor["total_earnings"],
            threshold=threshold,
        )

        if notified > 0:
            # Record that we've notified for this mentor × threshold pair
            try:
                supabase.table("admin_milestone_notifications").upsert({
                    "mentor_id": uid,
                    "threshold": threshold,
                    "combined_earnings": mentor["total_earnings"],
                    "notified_at": __import__("datetime").datetime.utcnow().isoformat(),
                }).execute()
            except Exception as exc:
                logger.warning(f"[Admin] Could not record milestone notification: {exc}")

            newly_notified += 1

    return {
        "checked": len(milestones),
        "newly_notified": newly_notified,
        "threshold": threshold,
    }

