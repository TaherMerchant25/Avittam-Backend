# =====================================================
# PAYMENTS ROUTES
# HTTP endpoints for payment management (Razorpay)
# =====================================================

from fastapi import APIRouter, Depends, Query
from typing import Optional
import hmac
import hashlib

from app.middleware.auth import get_current_user
from app.config.database import get_supabase_admin
from app.config.settings import settings
from app.models.schemas import (
    User,
    CreatePaymentOrder,
    VerifyPayment,
    PaymentStatus,
    ApiResponse,
)
from app.middleware.error_handler import BadRequestError, NotFoundError


router = APIRouter()


@router.post("/orders", response_model=ApiResponse)
async def create_payment_order(
    request: CreatePaymentOrder,
    user: User = Depends(get_current_user)
):
    """Create a Razorpay payment order"""
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise BadRequestError("Payment gateway not configured")
    
    import httpx
    
    # Create Razorpay order
    async with httpx.AsyncClient() as client:
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


@router.post("/verify", response_model=ApiResponse)
async def verify_payment(
    request: VerifyPayment,
    user: User = Depends(get_current_user)
):
    """Verify Razorpay payment signature"""
    if not settings.razorpay_key_secret:
        raise BadRequestError("Payment gateway not configured")
    
    # Verify signature
    message = f"{request.razorpay_order_id}|{request.razorpay_payment_id}"
    expected_signature = hmac.new(
        settings.razorpay_key_secret.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()
    
    if expected_signature != request.razorpay_signature:
        raise BadRequestError("Invalid payment signature")
    
    # Update payment record
    supabase = get_supabase_admin()
    
    from datetime import datetime
    
    result = supabase.table("payments").update({
        "razorpay_payment_id": request.razorpay_payment_id,
        "razorpay_signature": request.razorpay_signature,
        "status": PaymentStatus.PAID.value,
        "paid_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }).eq("razorpay_order_id", request.razorpay_order_id).eq("user_id", user.id).execute()
    
    if not result.data:
        raise NotFoundError("Payment record not found")
    
    return {
        "success": True,
        "data": result.data[0],
        "message": "Payment verified successfully",
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
    from datetime import datetime
    
    update_result = supabase.table("payments").update({
        "metadata": {"refund_requested": True, "refund_reason": reason},
        "updated_at": datetime.now().isoformat(),
    }).eq("id", payment_id).execute()
    
    return {
        "success": True,
        "data": update_result.data[0] if update_result.data else None,
        "message": "Refund request submitted. Our team will process it shortly.",
    }
