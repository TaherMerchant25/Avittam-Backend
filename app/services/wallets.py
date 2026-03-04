# =====================================================
# WALLET SERVICE
# Business logic for Avittam Coins, wallets, NPS fees,
# and referral commissions
# =====================================================

from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from loguru import logger
import hmac
import hashlib

from app.config.database import get_supabase_admin
from app.config.settings import settings
from app.middleware.error_handler import BadRequestError, NotFoundError


# =====================================================
# CONSTANTS
# =====================================================

COIN_RATE = 1.0  # 1 INR = 1 Avittam Coin
MENTOR_REGISTRATION_FEE = 10.0  # Yearly fee (X) for mentors

# MLM-Style Referral Commission Structure (for registration fee)
ORGANIZATION_SHARE_PCT = 25.0  # Bablu (organization) takes 25% of X
DIRECT_REFERRER_SHARE_PCT = 50.0  # Direct referrer gets 50% of X
LEVEL_2_SHARE_PCT = 12.5  # 2 levels up gets 12.5% of X
REMAINING_UPLINE_SHARE_PCT = 12.5  # Remaining 12.5% split among further uplines

# Rating-Based Bonus/Penalty for Session Earnings
# Base pay is 50% of session amount
BASE_PAY_PCT = 50.0
RATING_BONUS = {
    5: 30.0,  # +30% bonus
    4: 20.0,  # +20% bonus
    3: 0.0,   # No bonus/penalty
    2: -20.0, # -20% penalty
    1: -30.0  # -30% penalty
}


def get_nps_band(score: int) -> str:
    """Derive NPS band from score"""
    if score >= 9:
        return "promoter"
    elif score >= 6:
        return "passive"
    else:
        return "detractor"


def get_platform_fee_pct(rating: float) -> float:
    """
    Rating-based mentor earning calculation:
    - Base pay: 50% of session amount
    - Bonus/Penalty based on rating (rounded to nearest integer):
      * Rating 5: +30% → Total 80%
      * Rating 4: +20% → Total 70%
      * Rating 3: +0%  → Total 50%
      * Rating 2: -20% → Total 30%
      * Rating 1: -30% → Total 20%
    
    Returns platform fee percentage (100% - mentor_earning%)
    """
    rating_int = round(rating)
    if rating_int < 1:
        rating_int = 1
    elif rating_int > 5:
        rating_int = 5
    
    bonus = RATING_BONUS.get(rating_int, 0)
    mentor_earning_pct = BASE_PAY_PCT + bonus
    platform_fee_pct = 100.0 - mentor_earning_pct
    
    return platform_fee_pct


# =====================================================
# WALLET CRUD
# =====================================================

def get_or_create_wallet(user_id: str, wallet_type: str) -> Dict[str, Any]:
    """Get existing wallet or create a new one"""
    supabase = get_supabase_admin()
    
    result = supabase.table("wallets").select("*").eq(
        "user_id", user_id
    ).eq("type", wallet_type).execute()
    
    if result.data:
        return result.data[0]
    
    # Create new wallet
    new_wallet = {
        "user_id": user_id,
        "type": wallet_type,
        "balance": 0,
        "total_credited": 0,
        "total_debited": 0,
    }
    create_result = supabase.table("wallets").insert(new_wallet).execute()
    logger.info(f"Created {wallet_type} wallet for user {user_id}")
    return create_result.data[0]


def get_user_wallets(user_id: str) -> List[Dict[str, Any]]:
    """Get all wallets for a user"""
    supabase = get_supabase_admin()
    result = supabase.table("wallets").select("*").eq("user_id", user_id).execute()
    return result.data or []


def get_wallet_transactions(
    wallet_id: str, page: int = 1, limit: int = 20
) -> Tuple[List[Dict[str, Any]], int]:
    """Get paginated transactions for a wallet"""
    supabase = get_supabase_admin()
    offset = (page - 1) * limit
    
    result = supabase.table("wallet_transactions").select(
        "*", count="exact"
    ).eq("wallet_id", wallet_id).order(
        "created_at", desc=True
    ).range(offset, offset + limit - 1).execute()
    
    return result.data or [], result.count or 0


async def _get_referral_chain(supabase, referral_code: str) -> List[Dict[str, Any]]:
    """
    Get the referral chain (upline) for a given referral code.
    Returns list of mentors from direct referrer up to the top.
    """
    chain = []
    current_code = referral_code.upper()
    max_depth = 50  # Safety limit to prevent infinite loops
    
    for _ in range(max_depth):
        # Find mentor with this code
        result = supabase.table("users").select(
            "id, name, email, referral_code, referred_by"
        ).eq("referral_code", current_code).eq("role", "mentor").execute()
        
        if not result.data:
            break
        
        mentor = result.data[0]
        chain.append(mentor)
        
        # Move up the chain
        if not mentor.get("referred_by"):
            break
        
        # Get the referral code of the person who referred this mentor
        parent_result = supabase.table("users").select(
            "referral_code"
        ).eq("id", mentor["referred_by"]).execute()
        
        if not parent_result.data:
            break
        
        current_code = parent_result.data[0]["referral_code"]
    
    return chain


def get_mentor_wallet_overview(user_id: str) -> Dict[str, Any]:
    """Get mentor's mentorship + referral wallet overview"""
    wallets = get_user_wallets(user_id)
    
    mentorship = None
    referral = None
    
    for w in wallets:
        if w["type"] == "mentorship":
            mentorship = w
        elif w["type"] == "referral":
            referral = w
    
    # Create if missing
    if not mentorship:
        mentorship = get_or_create_wallet(user_id, "mentorship")
    if not referral:
        referral = get_or_create_wallet(user_id, "referral")
    
    return {
        "mentorship_wallet": mentorship,
        "referral_wallet": referral,
        "total_earnings": float(mentorship.get("total_credited", 0)),
        "total_referral_earnings": float(referral.get("total_credited", 0)),
    }


def get_student_wallet_overview(user_id: str) -> Dict[str, Any]:
    """Get student's Avittam Coin wallet overview"""
    wallet = get_or_create_wallet(user_id, "student")
    
    return {
        "wallet": wallet,
        "coin_balance": float(wallet.get("balance", 0)),
        "total_loaded": float(wallet.get("total_credited", 0)),
        "total_spent": float(wallet.get("total_debited", 0)),
    }


# =====================================================
# COIN LOADING (Student → Avittam Coins)
# =====================================================

async def create_coin_load_order(user_id: str, amount_inr: float) -> Dict[str, Any]:
    """
    Create a Razorpay order for loading Avittam Coins.
    1 INR = 1 Avittam Coin.
    """
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise BadRequestError("Payment gateway not configured")
    
    import httpx
    
    # Ensure wallet exists
    wallet = get_or_create_wallet(user_id, "student")
    coins_to_credit = amount_inr * COIN_RATE
    
    # Create Razorpay order
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.razorpay.com/v1/orders",
            auth=(settings.razorpay_key_id, settings.razorpay_key_secret),
            json={
                "amount": int(amount_inr * 100),  # paise
                "currency": "INR",
                "notes": {
                    "user_id": user_id,
                    "purpose": "coin_load",
                    "coins": coins_to_credit,
                },
            },
        )
        if response.status_code != 200:
            raise BadRequestError("Failed to create Razorpay order")
        order_data = response.json()
    
    # Store load order
    supabase = get_supabase_admin()
    load_order = {
        "user_id": user_id,
        "amount_inr": amount_inr,
        "coins_credited": coins_to_credit,
        "razorpay_order_id": order_data["id"],
        "status": "pending",
        "wallet_id": wallet["id"],
    }
    result = supabase.table("coin_load_orders").insert(load_order).execute()
    
    return {
        "order_id": result.data[0]["id"],
        "razorpay_order_id": order_data["id"],
        "amount_inr": amount_inr,
        "coins_to_credit": coins_to_credit,
        "key_id": settings.razorpay_key_id,
    }


def verify_coin_load(
    user_id: str,
    order_id: str,
    razorpay_order_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str,
) -> Dict[str, Any]:
    """Verify Razorpay payment and credit Avittam Coins to wallet"""
    if not settings.razorpay_key_secret:
        raise BadRequestError("Payment gateway not configured")
    
    # Verify signature
    message = f"{razorpay_order_id}|{razorpay_payment_id}"
    expected_sig = hmac.new(
        settings.razorpay_key_secret.encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()
    
    if expected_sig != razorpay_signature:
        raise BadRequestError("Invalid payment signature")
    
    supabase = get_supabase_admin()
    
    # Get the load order
    order_result = supabase.table("coin_load_orders").select("*").eq(
        "id", order_id
    ).eq("user_id", user_id).single().execute()
    
    if not order_result.data:
        raise NotFoundError("Coin load order not found")
    
    load_order = order_result.data
    
    if load_order["status"] == "paid":
        raise BadRequestError("Order already processed")
    
    coins = float(load_order["coins_credited"])
    wallet_id = load_order["wallet_id"]
    
    # Update load order
    supabase.table("coin_load_orders").update({
        "razorpay_payment_id": razorpay_payment_id,
        "razorpay_signature": razorpay_signature,
        "status": "paid",
    }).eq("id", order_id).execute()
    
    # Credit wallet
    wallet = supabase.table("wallets").select("*").eq("id", wallet_id).single().execute()
    current_balance = float(wallet.data["balance"])
    new_balance = current_balance + coins
    
    supabase.table("wallets").update({
        "balance": new_balance,
        "total_credited": float(wallet.data["total_credited"]) + coins,
    }).eq("id", wallet_id).execute()
    
    # Record transaction
    supabase.table("wallet_transactions").insert({
        "wallet_id": wallet_id,
        "tx_type": "credit",
        "category": "coin_load",
        "amount": coins,
        "balance_after": new_balance,
        "description": f"Loaded {coins} Avittam Coins (₹{load_order['amount_inr']})",
    }).execute()
    
    logger.info(f"Credited {coins} Avittam Coins to user {user_id}")
    
    return {
        "coins_credited": coins,
        "new_balance": new_balance,
        "amount_inr": load_order["amount_inr"],
    }


# =====================================================
# SESSION PAYMENT WITH AVITTAM COINS
# =====================================================

def pay_for_session_with_coins(
    mentee_id: str,
    session_id: str,
    mentor_id: str,
    total_coins: float,
    settle_immediately: bool = False,
) -> Dict[str, Any]:
    """
    Debit Avittam Coins from student's wallet to pay for a session.
    If settle_immediately=True, credit mentor right away at their current rating's rate.
    Otherwise the mentor payout happens AFTER session + NPS rating.
    """
    supabase = get_supabase_admin()
    
    # Get student wallet
    wallet = get_or_create_wallet(mentee_id, "student")
    balance = float(wallet["balance"])
    
    if balance < total_coins:
        raise BadRequestError(
            f"Insufficient Avittam Coins. Balance: {balance}, Required: {total_coins}"
        )
    
    # Debit student wallet
    new_balance = balance - total_coins
    supabase.table("wallets").update({
        "balance": new_balance,
        "total_debited": float(wallet["total_debited"]) + total_coins,
    }).eq("id", wallet["id"]).execute()
    
    # Record debit transaction
    supabase.table("wallet_transactions").insert({
        "wallet_id": wallet["id"],
        "tx_type": "debit",
        "category": "session_payment",
        "amount": total_coins,
        "balance_after": new_balance,
        "session_id": session_id,
        "related_user_id": mentor_id,
        "description": f"Paid {total_coins} Avittam Coins for session",
    }).execute()
    
    if settle_immediately:
        # Credit mentor immediately using their current rating
        mentor_result = supabase.table("users").select("rating").eq(
            "id", mentor_id
        ).single().execute()
        current_rating = (
            float(mentor_result.data.get("rating") or 3.0)
            if mentor_result.data
            else 3.0
        )
        fee_pct = get_platform_fee_pct(current_rating)
        mentor_earning = total_coins * (1.0 - fee_pct / 100.0)
        platform_fee = total_coins - mentor_earning
        
        mentor_wallet = get_or_create_wallet(mentor_id, "mentorship")
        mentor_balance = float(mentor_wallet["balance"])
        new_mentor_balance = mentor_balance + mentor_earning
        
        supabase.table("wallets").update({
            "balance": new_mentor_balance,
            "total_credited": float(mentor_wallet["total_credited"]) + mentor_earning,
        }).eq("id", mentor_wallet["id"]).execute()
        
        supabase.table("wallet_transactions").insert({
            "wallet_id": mentor_wallet["id"],
            "tx_type": "credit",
            "category": "session_earning",
            "amount": mentor_earning,
            "balance_after": new_mentor_balance,
            "session_id": session_id,
            "related_user_id": mentee_id,
            "description": (
                f"Session earning: {mentor_earning:.2f} coins "
                f"(rating {current_rating:.1f} → {fee_pct:.0f}% platform fee)"
            ),
        }).execute()
        
        # Create settled session_coin_payments record
        result = supabase.table("session_coin_payments").insert({
            "session_id": session_id,
            "mentee_id": mentee_id,
            "mentor_id": mentor_id,
            "total_coins": total_coins,
            "platform_fee_coins": platform_fee,
            "mentor_earning_coins": mentor_earning,
            "platform_fee_pct": fee_pct,
            "is_settled": True,
            "settled_at": datetime.now().isoformat(),
        }).execute()
        
        logger.info(
            f"Student {mentee_id} paid {total_coins} coins for session {session_id} "
            f"(settled immediately, mentor gets {mentor_earning:.2f})"
        )
        
        return {
            "session_coin_payment_id": result.data[0]["id"],
            "total_coins_paid": total_coins,
            "new_balance": new_balance,
            "mentor_earning": mentor_earning,
            "message": "Payment successful. Mentor credited immediately.",
        }
    
    # Unsettled – awaits NPS rating
    scp = {
        "session_id": session_id,
        "mentee_id": mentee_id,
        "mentor_id": mentor_id,
        "total_coins": total_coins,
        "platform_fee_coins": 0,       # Filled after NPS
        "mentor_earning_coins": 0,     # Filled after NPS
        "is_settled": False,
    }
    result = supabase.table("session_coin_payments").insert(scp).execute()
    
    logger.info(
        f"Student {mentee_id} paid {total_coins} coins for session {session_id}"
    )
    
    return {
        "session_coin_payment_id": result.data[0]["id"],
        "total_coins_paid": total_coins,
        "new_balance": new_balance,
        "message": "Payment successful. Mentor will receive earnings after session rating.",
    }


def credit_mentor_for_session_payment(
    mentor_id: str,
    session_id: str,
    amount_inr: float,
    mentee_id: str,
    description: str = "Session earning",
) -> Dict[str, Any]:
    """
    Credit mentor's mentorship wallet after a Razorpay session payment is verified.
    Uses mentor's current average rating to determine the platform fee split.
    1 INR is treated as 1 Avittam Coin for wallet credit purposes.
    """
    supabase = get_supabase_admin()
    
    # Use mentor's current rating for fee calculation (default 3.0 = 50%/50% split)
    mentor_result = supabase.table("users").select("rating").eq(
        "id", mentor_id
    ).single().execute()
    current_rating = (
        float(mentor_result.data.get("rating") or 3.0)
        if mentor_result.data
        else 3.0
    )
    fee_pct = get_platform_fee_pct(current_rating)
    mentor_earning = amount_inr * (1.0 - fee_pct / 100.0)
    platform_fee = amount_inr - mentor_earning
    
    # Credit mentor's mentorship wallet
    mentor_wallet = get_or_create_wallet(mentor_id, "mentorship")
    mentor_balance = float(mentor_wallet["balance"])
    new_mentor_balance = mentor_balance + mentor_earning
    
    supabase.table("wallets").update({
        "balance": new_mentor_balance,
        "total_credited": float(mentor_wallet["total_credited"]) + mentor_earning,
    }).eq("id", mentor_wallet["id"]).execute()
    
    # Record earning transaction
    supabase.table("wallet_transactions").insert({
        "wallet_id": mentor_wallet["id"],
        "tx_type": "credit",
        "category": "session_earning",
        "amount": mentor_earning,
        "balance_after": new_mentor_balance,
        "session_id": session_id,
        "related_user_id": mentee_id,
        "description": (
            f"{description}: {mentor_earning:.2f} coins "
            f"(rating {current_rating:.1f} → {fee_pct:.0f}% platform fee)"
        ),
    }).execute()
    
    # Create settled session_coin_payments record (Razorpay path — settled immediately)
    existing = supabase.table("session_coin_payments").select("id").eq(
        "session_id", session_id
    ).execute()
    if not existing.data:
        supabase.table("session_coin_payments").insert({
            "session_id": session_id,
            "mentee_id": mentee_id,
            "mentor_id": mentor_id,
            "total_coins": amount_inr,
            "platform_fee_coins": platform_fee,
            "mentor_earning_coins": mentor_earning,
            "platform_fee_pct": fee_pct,
            "is_settled": True,
            "settled_at": datetime.now().isoformat(),
        }).execute()
    
    logger.info(
        f"Credited {mentor_earning:.2f} coins to mentor {mentor_id} "
        f"for session {session_id} (Razorpay, fee={fee_pct:.0f}%)"
    )
    
    return {
        "mentor_earning": mentor_earning,
        "platform_fee": platform_fee,
        "fee_pct": fee_pct,
        "new_balance": new_mentor_balance,
    }


# =====================================================
# NPS RATING & SETTLEMENT
# =====================================================

def submit_nps_rating(
    rater_id: str,
    session_id: str,
    rated_mentor_id: str,
    score: int,
    feedback: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Submit rating (1-5) for a session.
    This triggers the settlement based on mentor's average rating:
    - Rating 5: +30% → Mentor gets 80%, Platform 20%
    - Rating 4: +20% → Mentor gets 70%, Platform 30%
    - Rating 3: +0%  → Mentor gets 50%, Platform 50%
    - Rating 2: -20% → Mentor gets 30%, Platform 70%
    - Rating 1: -30% → Mentor gets 20%, Platform 80%
    """
    supabase = get_supabase_admin()
    
    # Validate score (1-5 instead of 0-10)
    if score < 1 or score > 5:
        raise BadRequestError("Rating must be 1-5")
    
    band = get_nps_band(score)
    fee_pct = get_platform_fee_pct(score)
    
    # Check for duplicate
    existing = supabase.table("nps_ratings").select("id").eq(
        "session_id", session_id
    ).eq("rater_id", rater_id).execute()
    
    if existing.data:
        raise BadRequestError("You have already rated this session")
    
    # Get mentor's current average rating
    mentor_result = supabase.table("users").select("rating, sessions_completed").eq(
        "id", rated_mentor_id
    ).single().execute()
    
    current_rating = float(mentor_result.data.get("rating", 3.0) or 3.0)
    sessions_count = int(mentor_result.data.get("sessions_completed", 0) or 0)
    
    # Calculate new average rating
    new_rating = ((current_rating * sessions_count) + score) / (sessions_count + 1)
    
    # Use new rating to calculate fee
    fee_pct = get_platform_fee_pct(new_rating)
    
    # Update mentor's rating in users table
    supabase.table("users").update({
        "rating": new_rating,
        "sessions_completed": sessions_count + 1
    }).eq("id", rated_mentor_id).execute()
    
    # Insert rating record
    nps_data = {
        "session_id": session_id,
        "rater_id": rater_id,
        "rated_mentor_id": rated_mentor_id,
        "score": score,
        "band": "rating",
        "platform_fee_pct": fee_pct,
        "feedback": feedback,
    }
    nps_result = supabase.table("nps_ratings").insert(nps_data).execute()
    nps_id = nps_result.data[0]["id"]
    
    # Settle the session coin payment
    scp_result = supabase.table("session_coin_payments").select("*").eq(
        "session_id", session_id
    ).eq("is_settled", False).execute()
    
    settlement_info = None
    
    if scp_result.data:
        scp = scp_result.data[0]
        total = float(scp["total_coins"])
        platform_fee = total * (fee_pct / 100.0)
        mentor_earning = total - platform_fee
        
        # Update session_coin_payments
        supabase.table("session_coin_payments").update({
            "platform_fee_coins": platform_fee,
            "mentor_earning_coins": mentor_earning,
            "nps_rating_id": nps_id,
            "platform_fee_pct": fee_pct,
            "is_settled": True,
            "settled_at": datetime.now().isoformat(),
        }).eq("id", scp["id"]).execute()
        
        # Credit mentor's mentorship wallet
        mentor_wallet = get_or_create_wallet(rated_mentor_id, "mentorship")
        mentor_balance = float(mentor_wallet["balance"])
        new_mentor_balance = mentor_balance + mentor_earning
        
        supabase.table("wallets").update({
            "balance": new_mentor_balance,
            "total_credited": float(mentor_wallet["total_credited"]) + mentor_earning,
        }).eq("id", mentor_wallet["id"]).execute()
        
        # Record mentor earning transaction
        supabase.table("wallet_transactions").insert({
            "wallet_id": mentor_wallet["id"],
            "tx_type": "credit",
            "category": "session_earning",
            "amount": mentor_earning,
            "balance_after": new_mentor_balance,
            "session_id": session_id,
            "related_user_id": rater_id,
            "description": (
                f"Session earning: {mentor_earning} coins "
                f"(NPS {score} → {fee_pct}% platform fee)"
            ),
        }).execute()
        
        settlement_info = {
            "nps_score": score,
            "nps_band": band,
            "platform_fee_pct": fee_pct,
            "total_coins": total,
            "platform_fee_coins": platform_fee,
            "mentor_earning_coins": mentor_earning,
        }
        
        logger.info(
            f"Settled session {session_id}: "
            f"NPS={score}, fee={fee_pct}%, "
            f"mentor gets {mentor_earning} coins"
        )
    
    return {
        "nps_rating": nps_result.data[0],
        "settlement": settlement_info,
    }


# =====================================================
# MENTOR REGISTRATION FEE & REFERRAL COMMISSION
# =====================================================

async def create_registration_fee_order(
    mentor_id: str,
    amount: float,
    referral_code: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a Razorpay order for mentor registration fee with MLM-style commission:
    - 25% to organization (Bablu)
    - 50% to direct referrer
    - 12.5% to 2 levels up
    - 12.5% split among remaining uplines (to infinity, ends at Bablu)
    """
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise BadRequestError("Payment gateway not configured")
    
    import httpx
    
    supabase = get_supabase_admin()
    
    # Calculate commissions
    organization_share = amount * (ORGANIZATION_SHARE_PCT / 100.0)  # 25%
    direct_referrer_share = amount * (DIRECT_REFERRER_SHARE_PCT / 100.0)  # 50%
    level_2_share = amount * (LEVEL_2_SHARE_PCT / 100.0)  # 12.5%
    remaining_upline_share = amount * (REMAINING_UPLINE_SHARE_PCT / 100.0)  # 12.5%
    
    # Look up referral chain
    referral_chain = []
    if referral_code:
        referral_chain = await _get_referral_chain(supabase, referral_code)
    
    # Resolve referral attribution
    if referral_chain:
        referred_by_id = referral_chain[0]["id"]          # direct referrer
        referral_commission = direct_referrer_share        # 50% of fee
        platform_share = amount - referral_commission      # remaining 50%
    else:
        referred_by_id = None
        referral_commission = 0.0
        platform_share = amount                            # 100% to platform
    
    # Create Razorpay order
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.razorpay.com/v1/orders",
            auth=(settings.razorpay_key_id, settings.razorpay_key_secret),
            json={
                "amount": int(amount * 100),
                "currency": "INR",
                "notes": {
                    "mentor_id": mentor_id,
                    "purpose": "mentor_registration_fee",
                    "referral_code": referral_code or "",
                },
            },
        )
        if response.status_code != 200:
            raise BadRequestError("Failed to create payment order")
        order_data = response.json()
    
    # Store registration fee record
    fee_record = {
        "mentor_id": mentor_id,
        "amount": amount,
        "referred_by_id": referred_by_id,
        "referral_code": referral_code,
        "referral_commission": referral_commission,
        "platform_share": platform_share,
        "razorpay_order_id": order_data["id"],
        "is_paid": False,
    }
    result = supabase.table("mentor_registration_fees").insert(fee_record).execute()
    
    return {
        "fee_id": result.data[0]["id"],
        "razorpay_order_id": order_data["id"],
        "amount": amount,
        "referral_code": referral_code,
        "referral_commission": referral_commission,
        "platform_share": platform_share,
        "key_id": settings.razorpay_key_id,
    }


def verify_registration_fee(
    mentor_id: str,
    fee_id: str,
    razorpay_order_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str,
) -> Dict[str, Any]:
    """
    Verify mentor registration fee payment.
    If referral code was used, credit 40% to referrer's referral wallet.
    """
    if not settings.razorpay_key_secret:
        raise BadRequestError("Payment gateway not configured")
    
    # Verify signature
    message = f"{razorpay_order_id}|{razorpay_payment_id}"
    expected_sig = hmac.new(
        settings.razorpay_key_secret.encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()
    
    if expected_sig != razorpay_signature:
        raise BadRequestError("Invalid payment signature")
    
    supabase = get_supabase_admin()
    
    # Get fee record
    fee_result = supabase.table("mentor_registration_fees").select("*").eq(
        "id", fee_id
    ).eq("mentor_id", mentor_id).single().execute()
    
    if not fee_result.data:
        raise NotFoundError("Registration fee record not found")
    
    fee = fee_result.data
    
    if fee["is_paid"]:
        raise BadRequestError("Registration fee already paid")
    
    # Mark as paid
    supabase.table("mentor_registration_fees").update({
        "razorpay_payment_id": razorpay_payment_id,
        "is_paid": True,
        "paid_at": datetime.now().isoformat(),
    }).eq("id", fee_id).execute()
    
    # If referred, credit 40% to referrer's referral wallet
    referral_credited = False
    if fee["referred_by_id"] and float(fee["referral_commission"]) > 0:
        referrer_id = fee["referred_by_id"]
        commission = float(fee["referral_commission"])
        
        referral_wallet = get_or_create_wallet(referrer_id, "referral")
        ref_balance = float(referral_wallet["balance"])
        new_ref_balance = ref_balance + commission
        
        supabase.table("wallets").update({
            "balance": new_ref_balance,
            "total_credited": float(referral_wallet["total_credited"]) + commission,
        }).eq("id", referral_wallet["id"]).execute()
        
        # Record transaction
        supabase.table("wallet_transactions").insert({
            "wallet_id": referral_wallet["id"],
            "tx_type": "credit",
            "category": "referral_commission",
            "amount": commission,
            "balance_after": new_ref_balance,
            "related_user_id": mentor_id,
            "description": (
                f"Referral commission: {commission} coins "
                f"(50% of ₹{fee['amount']} registration fee)"
            ),
        }).execute()
        
        referral_credited = True
        logger.info(
            f"Credited {commission} coins to referrer {referrer_id}'s referral wallet"
        )
    
    logger.info(f"Mentor {mentor_id} registration fee paid: ₹{fee['amount']}")
    
    return {
        "fee_paid": True,
        "amount": fee["amount"],
        "referral_credited": referral_credited,
        "referral_commission": float(fee.get("referral_commission", 0)),
        "platform_share": float(fee.get("platform_share", 0)),
    }


# =====================================================
# WITHDRAWAL
# =====================================================

def request_withdrawal(
    user_id: str, wallet_type: str, amount: float
) -> Dict[str, Any]:
    """Request withdrawal from a wallet"""
    supabase = get_supabase_admin()
    
    wallet = get_or_create_wallet(user_id, wallet_type)
    balance = float(wallet["balance"])
    
    if balance < amount:
        raise BadRequestError(
            f"Insufficient balance. Available: {balance}, Requested: {amount}"
        )
    
    # Debit wallet
    new_balance = balance - amount
    supabase.table("wallets").update({
        "balance": new_balance,
        "total_debited": float(wallet["total_debited"]) + amount,
    }).eq("id", wallet["id"]).execute()
    
    # Record transaction
    supabase.table("wallet_transactions").insert({
        "wallet_id": wallet["id"],
        "tx_type": "debit",
        "category": "withdrawal",
        "amount": amount,
        "balance_after": new_balance,
        "description": f"Withdrawal of {amount} coins from {wallet_type} wallet",
    }).execute()
    
    logger.info(f"Withdrawal of {amount} from {wallet_type} wallet for user {user_id}")
    
    return {
        "withdrawn": amount,
        "new_balance": new_balance,
        "wallet_type": wallet_type,
    }
