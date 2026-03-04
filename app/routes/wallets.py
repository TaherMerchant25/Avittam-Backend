# =====================================================
# WALLET ROUTES
# HTTP endpoints for wallet, coins, NPS, referral fees
# =====================================================

import hashlib
import hmac as hmac_module
import json
from fastapi import APIRouter, Depends, Query, Request
from loguru import logger
from typing import Optional

from app.middleware.auth import get_current_user
from app.models.schemas import (
    User,
    ApiResponse,
    LoadCoinsRequest,
    LoadCoinsVerify,
    PayWithCoinsRequest,
    SubmitNPSRating,
    MentorRegistrationFeeRequest,
    MentorRegistrationFeeVerify,
    WithdrawalRequest,
    WalletType,
)
from app.services.wallets import (
    get_mentor_wallet_overview,
    get_student_wallet_overview,
    get_wallet_transactions,
    get_or_create_wallet,
    create_coin_load_order,
    verify_coin_load,
    pay_for_session_with_coins,
    submit_nps_rating,
    create_registration_fee_order,
    verify_registration_fee,
    request_withdrawal,
)


router = APIRouter()


# =====================================================
# WALLET OVERVIEW
# =====================================================

@router.get("/mentor/overview", response_model=ApiResponse)
async def get_mentor_wallets(user: User = Depends(get_current_user)):
    """Get mentor's mentorship + referral wallet overview"""
    overview = get_mentor_wallet_overview(user.id)
    return {"success": True, "data": overview}


@router.get("/student/overview", response_model=ApiResponse)
async def get_student_wallet(user: User = Depends(get_current_user)):
    """Get student's Avittam Coins wallet overview"""
    overview = get_student_wallet_overview(user.id)
    return {"success": True, "data": overview}


@router.get("/{wallet_type}/transactions", response_model=ApiResponse)
async def get_transactions(
    wallet_type: WalletType,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
):
    """Get transactions for a specific wallet"""
    wallet = get_or_create_wallet(user.id, wallet_type.value)
    transactions, total = get_wallet_transactions(wallet["id"], page, limit)
    total_pages = (total + limit - 1) // limit
    
    return {
        "success": True,
        "data": {
            "wallet": wallet,
            "transactions": transactions,
        },
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
            "total_pages": total_pages,
        },
    }


# =====================================================
# COIN LOADING (Student)
# =====================================================

@router.post("/coins/load", response_model=ApiResponse)
async def load_coins(
    request: LoadCoinsRequest,
    user: User = Depends(get_current_user),
):
    """Create a Razorpay order to load Avittam Coins (1 INR = 1 Coin)"""
    result = await create_coin_load_order(user.id, request.amount_inr)
    return {
        "success": True,
        "data": result,
        "message": f"Order created to load {request.amount_inr} Avittam Coins",
    }


@router.post("/coins/verify", response_model=ApiResponse)
async def verify_coin_load_payment(
    request: LoadCoinsVerify,
    user: User = Depends(get_current_user),
):
    """Verify Razorpay payment and credit Avittam Coins"""
    result = verify_coin_load(
        user_id=user.id,
        order_id=request.order_id,
        razorpay_order_id=request.razorpay_order_id,
        razorpay_payment_id=request.razorpay_payment_id,
        razorpay_signature=request.razorpay_signature,
    )
    return {
        "success": True,
        "data": result,
        "message": f"Successfully loaded {result['coins_credited']} Avittam Coins!",
    }


@router.get("/coins/verify-redirect")
async def verify_coin_load_redirect(
    order_id: str,
    razorpay_order_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str,
    frontend_url: str = "https://avittam.vercel.app",
):
    """
    Handle Razorpay redirect-based payments (NetBanking, some UPI apps).
    Razorpay POSTs/GETs here after redirect; we verify and redirect back to the frontend.
    """
    from fastapi.responses import RedirectResponse
    from app.config.database import get_supabase_admin

    try:
        # Find which user owns this order
        supabase = get_supabase_admin()
        order_result = supabase.table("coin_load_orders").select("user_id, coins_credited").eq(
            "razorpay_order_id", razorpay_order_id
        ).single().execute()

        if not order_result.data:
            return RedirectResponse(url=f"{frontend_url}?payment=failed&reason=order_not_found")

        user_id = order_result.data["user_id"]

        result = verify_coin_load(
            user_id=user_id,
            order_id=order_id,
            razorpay_order_id=razorpay_order_id,
            razorpay_payment_id=razorpay_payment_id,
            razorpay_signature=razorpay_signature,
        )
        coins = result.get("coins_credited", 0)
        return RedirectResponse(
            url=f"{frontend_url}?payment=success&coins={coins}&payment_id={razorpay_payment_id}"
        )
    except Exception as e:
        logger.error(f"Redirect verify failed: {e}")
        return RedirectResponse(url=f"{frontend_url}?payment=failed&reason=verification_error")


@router.post("/coins/webhook")
async def razorpay_coin_webhook(request: Request):
    """
    Razorpay webhook endpoint — receives payment.captured events.
    This is the RELIABLE way to credit coins: Razorpay's servers call this
    directly, regardless of whether the user's browser is open.
    Configure in Razorpay Dashboard → Settings → Webhooks.
    """
    from app.config.database import get_supabase_admin
    from app.config.settings import settings

    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")

    # Verify webhook signature if secret is configured
    if settings.razorpay_webhook_secret:
        expected = hmac_module.new(
            settings.razorpay_webhook_secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac_module.compare_digest(expected, signature):
            logger.warning("Razorpay webhook: invalid signature — ignoring")
            return {"status": "ok"}  # Return 200 so Razorpay doesn't retry endlessly
    else:
        logger.warning("Razorpay webhook: RAZORPAY_WEBHOOK_SECRET not set — skipping signature check")

    try:
        payload = json.loads(body)
    except Exception:
        return {"status": "ok"}

    event = payload.get("event", "")
    logger.info(f"Razorpay webhook received: {event}")

    if event == "payment.captured":
        payment = payload.get("payload", {}).get("payment", {}).get("entity", {})
        razorpay_order_id = payment.get("order_id")
        razorpay_payment_id = payment.get("id")

        if not razorpay_order_id:
            return {"status": "ok"}

        try:
            supabase = get_supabase_admin()

            # Find the order
            order_result = supabase.table("coin_load_orders").select("*").eq(
                "razorpay_order_id", razorpay_order_id
            ).single().execute()

            if not order_result.data:
                logger.info(f"Webhook: no coin order for razorpay_order_id={razorpay_order_id}, skipping")
                return {"status": "ok"}

            order = order_result.data

            if order["status"] == "paid":
                logger.info(f"Webhook: order {order['id']} already paid, skipping")
                return {"status": "ok"}

            coins = float(order["coins_credited"])
            wallet_id = order["wallet_id"]

            # Mark order paid
            supabase.table("coin_load_orders").update({
                "razorpay_payment_id": razorpay_payment_id,
                "status": "paid",
            }).eq("id", order["id"]).execute()

            # Credit wallet
            wallet = supabase.table("wallets").select("*").eq("id", wallet_id).single().execute()
            current_balance = float(wallet.data["balance"])
            new_balance = current_balance + coins

            supabase.table("wallets").update({
                "balance": new_balance,
                "total_credited": float(wallet.data["total_credited"]) + coins,
            }).eq("id", wallet_id).execute()

            supabase.table("wallet_transactions").insert({
                "wallet_id": wallet_id,
                "tx_type": "credit",
                "category": "coin_load",
                "amount": coins,
                "balance_after": new_balance,
                "description": f"Loaded {coins:.0f} Avittam Coins (webhook)",
            }).execute()

            logger.info(f"Webhook: credited {coins:.0f} coins to wallet {wallet_id} for order {order['id']}")

        except Exception as e:
            logger.error(f"Webhook coin credit failed: {e}")

    return {"status": "ok"}


@router.get("/coins/check-payment", response_model=ApiResponse)
async def check_coin_payment_status(
    razorpay_order_id: str,
    user: User = Depends(get_current_user),
):
    """
    Poll Razorpay's API directly to check if an order has been paid.
    Credits coins if payment is captured but not yet credited.
    Called by the frontend every few seconds while waiting for UPI/QR confirmation.
    """
    import httpx
    from app.config.database import get_supabase_admin
    from app.config.settings import settings

    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        return {"success": False, "data": {"status": "unknown"}, "message": "Payment gateway not configured"}

    supabase = get_supabase_admin()

    # Look up our order record
    order_result = supabase.table("coin_load_orders").select("*").eq(
        "razorpay_order_id", razorpay_order_id
    ).eq("user_id", user.id).single().execute()

    if not order_result.data:
        return {"success": False, "data": {"status": "not_found"}, "message": "Order not found"}

    order = order_result.data

    # Already credited — just return success
    if order["status"] == "paid":
        return {
            "success": True,
            "data": {"status": "paid", "coins_credited": order["coins_credited"]},
            "message": f"Payment already processed — {order['coins_credited']:.0f} coins credited",
        }

    # Ask Razorpay for the list of payments on this order
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://api.razorpay.com/v1/orders/{razorpay_order_id}/payments",
                auth=(settings.razorpay_key_id, settings.razorpay_key_secret),
            )
        logger.info(f"check-payment: Razorpay status={resp.status_code} body={resp.text[:500]}")
        if resp.status_code != 200:
            logger.warning(f"check-payment: Razorpay API returned {resp.status_code}: {resp.text}")
            return {"success": False, "data": {"status": "pending"}, "message": "Waiting for payment"}

        data = resp.json()
        payments = data.get("items", [])
        statuses = [p.get("status") for p in payments]
        logger.info(f"check-payment: order={razorpay_order_id} payment_count={len(payments)} statuses={statuses}")
    except Exception as e:
        logger.error(f"check-payment: Razorpay API call failed: {e}")
        return {"success": False, "data": {"status": "pending"}, "message": "Waiting for payment"}

    # UPI QR / live mode: payments go through "authorized" first, then "captured".
    # We treat both as a successful payment and credit coins immediately.
    successful = next(
        (p for p in payments if p.get("status") in ("captured", "authorized")), None
    )
    if not successful:
        failed = next((p for p in payments if p.get("status") == "failed"), None)
        if failed:
            return {"success": False, "data": {"status": "failed"}, "message": "Payment failed"}
        return {"success": False, "data": {"status": "pending"}, "message": "Waiting for payment confirmation"}

    # If payment is only authorized (not yet captured), try to capture it
    razorpay_payment_id = successful["id"]
    if successful.get("status") == "authorized":
        logger.info(f"check-payment: payment {razorpay_payment_id} is authorized — capturing")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                cap_resp = await client.post(
                    f"https://api.razorpay.com/v1/payments/{razorpay_payment_id}/capture",
                    auth=(settings.razorpay_key_id, settings.razorpay_key_secret),
                    json={"amount": successful.get("amount", 0), "currency": "INR"},
                )
            logger.info(f"check-payment: capture response {cap_resp.status_code}: {cap_resp.text[:200]}")
        except Exception as e:
            logger.warning(f"check-payment: capture call failed (may auto-capture): {e}")

    # Payment captured — credit coins (idempotent)
    coins = float(order["coins_credited"])
    wallet_id = order["wallet_id"]

    # Mark order paid
    supabase.table("coin_load_orders").update({
        "razorpay_payment_id": razorpay_payment_id,
        "status": "paid",
    }).eq("id", order["id"]).execute()

    # Credit wallet
    wallet = supabase.table("wallets").select("*").eq("id", wallet_id).single().execute()
    current_balance = float(wallet.data["balance"])
    new_balance = current_balance + coins

    supabase.table("wallets").update({
        "balance": new_balance,
        "total_credited": float(wallet.data["total_credited"]) + coins,
    }).eq("id", wallet_id).execute()

    supabase.table("wallet_transactions").insert({
        "wallet_id": wallet_id,
        "tx_type": "credit",
        "category": "coin_load",
        "amount": coins,
        "balance_after": new_balance,
        "description": f"Loaded {coins:.0f} Avittam Coins (₹{order['amount_inr']})",
    }).execute()

    logger.info(f"check-payment: credited {coins:.0f} coins via status poll for order {order['id']}")
    return {
        "success": True,
        "data": {"status": "paid", "coins_credited": coins, "new_balance": new_balance},
        "message": f"✅ Payment confirmed! {coins:.0f} Avittam Coins added.",
    }


# =====================================================
# SESSION PAYMENT WITH COINS
# =====================================================

@router.post("/coins/pay-session", response_model=ApiResponse)
async def pay_session_with_coins(
    request: PayWithCoinsRequest,
    user: User = Depends(get_current_user),
):
    """Pay for a session using Avittam Coins"""
    result = pay_for_session_with_coins(
        mentee_id=user.id,
        session_id=request.session_id,
        mentor_id=request.mentor_id,
        total_coins=request.total_coins,
    )
    return {
        "success": True,
        "data": result,
        "message": "Session paid with Avittam Coins",
    }


# =====================================================
# NPS RATING & SETTLEMENT
# =====================================================

@router.post("/nps/rate", response_model=ApiResponse)
async def rate_session_nps(
    request: SubmitNPSRating,
    user: User = Depends(get_current_user),
):
    """
    Submit NPS rating (0-10) for a completed session.
    Platform fee:
      9-10 → 20% (mentor gets 80%)
      6-8  → 40% (mentor gets 60%)
      0-5  → 60% (mentor gets 40%)
    """
    # Get session to find the mentor
    from app.config.database import get_supabase_admin
    supabase = get_supabase_admin()
    
    session_result = supabase.table("sessions").select("mentor_id").eq(
        "id", request.session_id
    ).single().execute()
    
    if not session_result.data:
        from app.middleware.error_handler import NotFoundError
        raise NotFoundError("Session not found")
    
    result = submit_nps_rating(
        rater_id=user.id,
        session_id=request.session_id,
        rated_mentor_id=session_result.data["mentor_id"],
        score=request.score,
        feedback=request.feedback,
    )
    return {
        "success": True,
        "data": result,
        "message": f"NPS rating submitted. Platform fee: {result['settlement']['platform_fee_pct']}%"
        if result.get("settlement") else "NPS rating submitted",
    }


# =====================================================
# MENTOR REGISTRATION FEE
# =====================================================

@router.post("/registration-fee/create", response_model=ApiResponse)
async def create_reg_fee_order(
    request: MentorRegistrationFeeRequest,
    user: User = Depends(get_current_user),
):
    """Create Razorpay order for mentor registration fee.
    If referral_code is provided, 40% goes to the referrer's wallet."""
    result = await create_registration_fee_order(
        mentor_id=user.id,
        amount=request.amount,
        referral_code=request.referral_code,
    )
    return {
        "success": True,
        "data": result,
        "message": "Registration fee order created",
    }


@router.post("/registration-fee/verify", response_model=ApiResponse)
async def verify_reg_fee(
    request: MentorRegistrationFeeVerify,
    user: User = Depends(get_current_user),
):
    """Verify mentor registration fee payment.
    Credits 40% to referrer's referral wallet if applicable."""
    result = verify_registration_fee(
        mentor_id=user.id,
        fee_id=request.fee_id,
        razorpay_order_id=request.razorpay_order_id,
        razorpay_payment_id=request.razorpay_payment_id,
        razorpay_signature=request.razorpay_signature,
    )
    return {
        "success": True,
        "data": result,
        "message": "Registration fee paid successfully",
    }


@router.get("/referral-code", response_model=ApiResponse)
async def get_my_referral_code(
    user: User = Depends(get_current_user),
):
    """Get current user's referral code (mentors only)"""
    from ..config.database import get_supabase_admin
    
    supabase = get_supabase_admin()
    result = supabase.table("users").select("referral_code, name, role").eq(
        "id", user.id
    ).single().execute()
    
    if not result.data:
        raise NotFoundError("User not found")
    
    if result.data["role"] != "mentor":
        raise BadRequestError("Only mentors have referral codes")
    
    return {
        "success": True,
        "data": {
            "referral_code": result.data.get("referral_code"),
            "name": result.data.get("name"),
        },
        "message": "Referral code retrieved successfully",
    }


# =====================================================
# WITHDRAWAL
# =====================================================

@router.post("/withdraw", response_model=ApiResponse)
async def withdraw_from_wallet(
    request: WithdrawalRequest,
    user: User = Depends(get_current_user),
):
    """Withdraw coins from a wallet"""
    result = request_withdrawal(
        user_id=user.id,
        wallet_type=request.wallet_type.value,
        amount=request.amount,
    )
    return {
        "success": True,
        "data": result,
        "message": f"Withdrawal of {request.amount} coins processed",
    }


# =====================================================
# NPS FEE SCHEDULE (Public info)
# =====================================================

@router.get("/nps/fee-schedule", response_model=ApiResponse)
async def get_nps_fee_schedule():
    """Get the NPS-based platform fee schedule"""
    return {
        "success": True,
        "data": {
            "schedule": [
                {
                    "band": "promoter",
                    "score_range": "9-10",
                    "platform_fee_pct": 20,
                    "mentor_earning_pct": 80,
                    "description": "Excellent! Mentor gets 80% of session fee",
                },
                {
                    "band": "passive",
                    "score_range": "6-8",
                    "platform_fee_pct": 40,
                    "mentor_earning_pct": 60,
                    "description": "Good. Mentor gets 60% of session fee",
                },
                {
                    "band": "detractor",
                    "score_range": "0-5",
                    "platform_fee_pct": 60,
                    "mentor_earning_pct": 40,
                    "description": "Needs improvement. Mentor gets 40% of session fee",
                },
            ],
            "coin_rate": "1 INR = 1 Avittam Coin",
            "referral_commission_pct": 40,
        },
    }
