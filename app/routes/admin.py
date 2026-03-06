from typing import List, Dict, Any, Optional

from fastapi import APIRouter, Depends, Query, Body

from app.middleware.auth import require_admin
from app.models.schemas import User, ApiResponse
from app.services import admin as admin_service
from app.config.database import get_supabase_admin
from app.middleware.error_handler import BadRequestError, NotFoundError
from loguru import logger
from pydantic import BaseModel


router = APIRouter()


class CoinAdjustRequest(BaseModel):
    user_id: str
    action: str          # "set" | "add" | "deduct"
    amount: int
    reason: Optional[str] = "Admin adjustment"


@router.get("/mentor-milestones", response_model=ApiResponse)
async def get_mentor_milestones(
    threshold: float = Query(10_000, ge=0),
    user: User = Depends(require_admin),
) -> ApiResponse:
    """
    List mentors whose **combined** wallet earnings (mentorship + referral
    total_credited) are at or above the given threshold.

    Each mentor record includes:
    - `mentorship_earnings` — total credited to the mentorship wallet
    - `referral_earnings`   — total credited to the referral wallet
    - `total_earnings`      — combined total (used for threshold comparison)
    """
    mentors: List[Dict[str, Any]] = await admin_service.get_mentor_milestones(threshold)

    return ApiResponse(
        success=True,
        data={
            "threshold": threshold,
            "count": len(mentors),
            "mentors": mentors,
        },
        message="Mentor earning milestones fetched successfully",
    )


@router.post("/check-milestones", response_model=ApiResponse)
async def check_milestones(
    threshold: float = Query(10_000, ge=0),
    user: User = Depends(require_admin),
) -> ApiResponse:
    """
    Trigger a milestone check: scan all mentors, find those who crossed the
    threshold for the first time, and dispatch in-app notifications to all
    admin users.

    Safe to call repeatedly — already-notified milestones are de-duplicated
    via the `admin_milestone_notifications` table.
    """
    result = await admin_service.check_and_notify_milestones(threshold)

    return ApiResponse(
        success=True,
        data=result,
        message=(
            f"Milestone check complete — {result['newly_notified']} new notification(s) sent."
        ),
    )


# =====================================================
# GET /api/admin/users — list all users (admin only)
# =====================================================
@router.get("/users", response_model=ApiResponse)
async def list_all_users(
    search: Optional[str] = Query(None, description="Filter by name or email"),
    user: User = Depends(require_admin),
) -> ApiResponse:
    """Return all users with their current coin balance (admin only)."""
    supabase = get_supabase_admin()

    query = supabase.table("users").select(
        "id, name, email, role"
    ).order("name")

    if search:
        # Supabase ilike filter — search name OR email
        query = query.or_(f"name.ilike.%{search}%,email.ilike.%{search}%")

    result = query.limit(200).execute()
    users = result.data or []

    # Fetch combined wallet balances (mentorship + referral) for all returned users
    if users:
        user_ids = [u["id"] for u in users]
        wallets_res = supabase.table("wallets").select(
            "user_id, balance, type"
        ).in_("type", ["mentorship", "referral"]).in_("user_id", user_ids).execute()
        # Sum mentorship + referral into a single coin_balance per user
        balance_map: dict = {}
        for w in (wallets_res.data or []):
            uid = w["user_id"]
            balance_map[uid] = balance_map.get(uid, 0) + (w["balance"] or 0)
        for u in users:
            u["coin_balance"] = balance_map.get(u["id"], 0)

    return ApiResponse(success=True, data={"users": users, "count": len(users)})


# =====================================================
# POST /api/admin/coins/adjust — set/add/deduct coins
# =====================================================
@router.post("/coins/adjust", response_model=ApiResponse)
async def admin_adjust_coins(
    body: CoinAdjustRequest,
    admin_user: User = Depends(require_admin),
) -> ApiResponse:
    """
    Adjust Avittam Coins for any user (admin only).
    - action = 'set'    → set balance to `amount`
    - action = 'add'    → credit `amount` coins
    - action = 'deduct' → debit `amount` coins (capped at current balance)
    """
    if body.action not in ("set", "add", "deduct"):
        raise BadRequestError("action must be 'set', 'add', or 'deduct'")
    if body.amount < 0:
        raise BadRequestError("amount must be non-negative")

    supabase = get_supabase_admin()

    # Verify target user exists
    user_res = supabase.table("users").select("id, name, email").eq("id", body.user_id).execute()
    if not user_res.data:
        raise NotFoundError("User not found")
    target_user = user_res.data[0]

    # Get or create mentorship wallet (used as the admin-adjustable coin wallet)
    wallet_res = supabase.table("wallets").select("*").eq(
        "user_id", body.user_id
    ).eq("type", "mentorship").execute()

    if wallet_res.data:
        wallet = wallet_res.data[0]
    else:
        new_wallet = supabase.table("wallets").insert({
            "user_id": body.user_id,
            "type": "mentorship",
            "balance": 0,
            "total_credited": 0,
            "total_debited": 0,
        }).execute()
        wallet = new_wallet.data[0]

    old_balance = int(wallet["balance"] or 0)
    wallet_id = wallet["id"]

    if body.action == "set":
        new_balance = body.amount
        delta = new_balance - old_balance
        txn_type = "admin_credit" if delta >= 0 else "admin_deduct"
        txn_amount = abs(delta)
    elif body.action == "add":
        delta = body.amount
        new_balance = old_balance + delta
        txn_type = "admin_credit"
        txn_amount = delta
    else:  # deduct
        delta = min(body.amount, old_balance)  # don't go negative
        new_balance = old_balance - delta
        txn_type = "admin_deduct"
        txn_amount = delta

    # Update wallet
    total_credited = int(wallet.get("total_credited") or 0)
    total_debited = int(wallet.get("total_debited") or 0)
    if txn_type == "admin_credit":
        total_credited += txn_amount
    else:
        total_debited += txn_amount

    supabase.table("wallets").update({
        "balance": new_balance,
        "total_credited": total_credited,
        "total_debited": total_debited,
    }).eq("id", wallet_id).execute()

    # Insert transaction record
    if txn_amount > 0:
        supabase.table("wallet_transactions").insert({
            "wallet_id": wallet_id,
            "tx_type": "credit" if txn_type == "admin_credit" else "debit",
            "category": txn_type,   # "admin_credit" or "admin_deduct"
            "amount": txn_amount,
            "balance_after": new_balance,
            "description": f"[Admin] {body.reason or 'Manual adjustment'} (by {admin_user.email})",
            "metadata": {
                "admin_id": admin_user.id,
                "admin_email": admin_user.email,
                "action": body.action,
                "old_balance": old_balance,
            },
        }).execute()

    logger.info(
        f"[Admin] Coins adjusted for {target_user['email']}: "
        f"{old_balance} → {new_balance} (action={body.action}, amount={body.amount}, "
        f"by={admin_user.email})"
    )

    return ApiResponse(
        success=True,
        data={
            "user_id": body.user_id,
            "user_name": target_user.get("name"),
            "user_email": target_user.get("email"),
            "old_balance": old_balance,
            "new_balance": new_balance,
            "action": body.action,
            "amount_applied": txn_amount,
        },
        message=f"Coins updated: {old_balance} → {new_balance}",
    )
