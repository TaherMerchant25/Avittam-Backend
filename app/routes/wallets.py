# =====================================================
# WALLET ROUTES
# HTTP endpoints for wallet, coins, NPS, referral fees
# =====================================================

from fastapi import APIRouter, Depends, Query
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
