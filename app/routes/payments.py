# =====================================================
# PAYMENTS ROUTES
# HTTP endpoints for payment management (Razorpay)
# =====================================================

from fastapi import APIRouter, Depends, Query
from typing import Optional
import hmac
import hashlib
import httpx
from datetime import datetime, timedelta

from app.middleware.auth import get_current_user, get_optional_user
from app.config.database import get_supabase_admin
from app.config.settings import settings
from app.models.schemas import (
    User,
    CreatePaymentOrder,
    CreateRegistrationOrder,
    VerifyPayment,
    PaymentStatus,
    ApiResponse,
)
from app.middleware.error_handler import BadRequestError, NotFoundError


router = APIRouter()


# =====================================================
# MENTEE REGISTRATION (unauthenticated)
# =====================================================

@router.post("/create-order")
async def create_registration_order(request: CreateRegistrationOrder):
    """
    Create Razorpay order for mentee registration fee.
    No auth required - used before user account exists.
    """
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        return {"success": False, "error": "Payment gateway not configured"}

    # Create Razorpay order (amount already in paise)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                "https://api.razorpay.com/v1/orders",
                auth=(settings.razorpay_key_id, settings.razorpay_key_secret),
                json={
                    "amount": request.amount,
                    "currency": "INR",
                    "notes": {
                        "type": "mentee_registration",
                        "email": request.email,
                    },
                },
            )
    except httpx.TimeoutException:
        return {"success": False, "error": "Payment gateway timed out. Please try again."}
    except httpx.RequestError as e:
        return {"success": False, "error": f"Could not reach payment gateway: {str(e)}"}

    if response.status_code != 200:
        return {"success": False, "error": "Failed to create payment order"}

    order_data = response.json()

    # Split name into first/last
    parts = request.name.strip().split(maxsplit=1)
    first_name = parts[0] if parts else ""
    last_name = parts[1] if len(parts) > 1 else ""

    # Upsert pending_registrations
    supabase = get_supabase_admin()
    reg_data = {
        "email": request.email,
        "first_name": first_name,
        "last_name": last_name,
        "profile_data": {},
        "registration_type": "mentee",
        "payment_status": "processing",
        "payment_amount": request.amount / 100.0,  # paise to rupees
        "payment_currency": "INR",
        "razorpay_order_id": order_data["id"],
        "expires_at": (datetime.now() + timedelta(hours=24)).isoformat(),
    }

    try:
        supabase.table("pending_registrations").upsert(
            reg_data,
            on_conflict="email",
        ).execute()
    except Exception:
        pass  # Continue even if upsert fails - order was created

    return {
        "success": True,
        "key_id": settings.razorpay_key_id,
        "order": {
            "id": order_data["id"],
            "amount": order_data["amount"],
            "currency": order_data["currency"],
        },
    }


@router.post("/verify")
async def verify_payment_any(
    request: VerifyPayment,
    user: Optional[User] = Depends(get_optional_user),
):
    """
    Verify Razorpay payment. Works for:
    - Authenticated: session/mentorship payments (payments table)
    - Unauthenticated: mentee registration (pending_registrations table)
    """
    if not settings.razorpay_key_secret:
        raise BadRequestError("Payment gateway not configured")

    message = f"{request.razorpay_order_id}|{request.razorpay_payment_id}"
    expected_signature = hmac.new(
        settings.razorpay_key_secret.encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()

    if expected_signature != request.razorpay_signature:
        raise BadRequestError("Invalid payment signature")

    supabase = get_supabase_admin()

    if user:
        # Authenticated: update payments table
        result = (
            supabase.table("payments")
            .update(
                {
                    "razorpay_payment_id": request.razorpay_payment_id,
                    "razorpay_signature": request.razorpay_signature,
                    "status": PaymentStatus.PAID.value,
                    "paid_at": datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat(),
                }
            )
            .eq("razorpay_order_id", request.razorpay_order_id)
            .eq("user_id", user.id)
            .execute()
        )
        if not result.data:
            raise NotFoundError("Payment record not found")
        return {
            "success": True,
            "data": result.data[0],
            "message": "Payment verified successfully",
        }

    # Unauthenticated: update pending_registrations (mentee registration)
    result = (
        supabase.table("pending_registrations")
        .update(
            {
                "razorpay_payment_id": request.razorpay_payment_id,
                "razorpay_signature": request.razorpay_signature,
                "payment_status": "completed",
                "updated_at": datetime.now().isoformat(),
            }
        )
        .eq("razorpay_order_id", request.razorpay_order_id)
        .execute()
    )
    if not result.data:
        raise NotFoundError("Registration payment record not found")
    return {"success": True, "message": "Payment verified successfully"}


# =====================================================
# AUTHENTICATED PAYMENTS
# =====================================================

@router.post("/orders", response_model=ApiResponse)
async def create_payment_order(
    request: CreatePaymentOrder,
    user: User = Depends(get_current_user)
):
    """Create a Razorpay payment order"""
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise BadRequestError("Payment gateway not configured")
    
    # Create Razorpay order
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                "https://api.razorpay.com/v1/orders",
                auth=(settings.razorpay_key_id, settings.razorpay_key_secret),
                json={
                    "amount": int(request.amount * 100),  # Amount in paise
                    "currency": request.currency,
                    "notes": {
                        "user_id": user.id,
                        "session_id": request.session_id,
                        "description": request.description,
                    },
                },
            )
    except httpx.TimeoutException:
        raise BadRequestError("Payment gateway timed out. Please try again.")
    except httpx.RequestError as e:
        raise BadRequestError(f"Could not reach payment gateway: {str(e)}")

    if response.status_code != 200:
        raise BadRequestError("Failed to create payment order")

    order_data = response.json()
    
    # Store payment record
    supabase = get_supabase_admin()
    payment_data = {
        "user_id": user.id,
        "session_id": request.session_id,
        "long_term_mentorship_id": request.long_term_mentorship_id,
        "razorpay_order_id": order_data["id"],
        "amount": request.amount,
        "currency": request.currency,
        "status": PaymentStatus.PENDING.value,
        "description": request.description,
    }
    
    result = supabase.table("payments").insert(payment_data).execute()
    
    return {
        "success": True,
        "data": {
            "order_id": order_data["id"],
            "amount": order_data["amount"],
            "currency": order_data["currency"],
            "key_id": settings.razorpay_key_id,
            "payment_id": result.data[0]["id"] if result.data else None,
        },
        "message": "Payment order created",
    }


@router.get("/history", response_model=ApiResponse)
async def get_payment_history(
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user)
):
    """Get user's payment history"""
    supabase = get_supabase_admin()
    
    query = supabase.table("payments").select("*", count="exact").eq("user_id", user.id)
    
    if status:
        query = query.eq("status", status)
    
    offset = (page - 1) * limit
    query = query.order("created_at", desc=True).range(offset, offset + limit - 1)
    
    result = query.execute()
    
    total_pages = ((result.count or 0) + limit - 1) // limit
    
    return {
        "success": True,
        "data": result.data or [],
        "pagination": {
            "page": page,
            "limit": limit,
            "total": result.count or 0,
            "total_pages": total_pages,
        },
    }


@router.get("/{payment_id}", response_model=ApiResponse)
async def get_payment(
    payment_id: str,
    user: User = Depends(get_current_user)
):
    """Get payment details"""
    supabase = get_supabase_admin()
    
    result = supabase.table("payments").select("*").eq(
        "id", payment_id
    ).eq("user_id", user.id).single().execute()
    
    if not result.data:
        raise NotFoundError("Payment not found")
    
    return {
        "success": True,
        "data": result.data,
    }


@router.post("/{payment_id}/refund", response_model=ApiResponse)
async def request_refund(
    payment_id: str,
    reason: Optional[str] = None,
    user: User = Depends(get_current_user)
):
    """Request a refund (for admins to process)"""
    supabase = get_supabase_admin()
    
    # Get payment
    result = supabase.table("payments").select("*").eq(
        "id", payment_id
    ).eq("user_id", user.id).single().execute()
    
    if not result.data:
        raise NotFoundError("Payment not found")
    
    payment = result.data
    
    if payment["status"] != PaymentStatus.PAID.value:
        raise BadRequestError("Can only refund paid payments")
    
    # For now, just mark as refund requested (admin would process manually)
    update_result = supabase.table("payments").update({
        "metadata": {"refund_requested": True, "refund_reason": reason},
        "updated_at": datetime.now().isoformat(),
    }).eq("id", payment_id).execute()
    
    return {
        "success": True,
        "data": update_result.data[0] if update_result.data else None,
        "message": "Refund request submitted. Our team will process it shortly.",
    }
