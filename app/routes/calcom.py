# =====================================================
# CAL.COM BOOKING ROUTES
# HTTP endpoints for mentor-initiated Cal.com scheduling
# =====================================================

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional
from loguru import logger

from app.middleware.auth import get_current_user
from app.models.schemas import User, ApiResponse
from app.services.calcom import calcom_service
from app.config.database import get_supabase_admin


router = APIRouter()


class CreateCalComBookingRequest(BaseModel):
    """Request to create a Cal.com booking (mentor-initiated)"""
    request_id: str = Field(..., description="Mentor request ID")
    mentee_id: str = Field(..., description="Student user ID")
    mentee_email: str = Field(..., description="Student email")
    mentee_name: str = Field(..., description="Student name")
    start_time: datetime = Field(..., description="Meeting start time (ISO 8601)")
    duration_minutes: int = Field(default=60, description="Session duration")
    event_type_id: int = Field(..., description="Cal.com event type ID")
    notes: Optional[str] = Field(None, description="Session notes")


@router.post("/create-booking", response_model=ApiResponse)
async def create_mentor_booking(
    request: CreateCalComBookingRequest,
    user: User = Depends(get_current_user)
):
    """
    Create a Cal.com booking initiated by mentor after request acceptance & payment.
    
    Flow:
    1. Verify mentor owns the request
    2. Verify request is accepted and paid
    3. Create booking via Cal.com API
    4. Save session to database with meeting URL
    5. Update request status to 'scheduled'
    """
    
    # Only mentors can create bookings
    if user.role.value != "mentor":
        raise HTTPException(status_code=403, detail="Only mentors can schedule sessions")
    
    supabase = get_supabase_admin()
    
    try:
        # 1. Verify mentor owns this request and it's accepted
        req_response = supabase.table("mentor_requests").select(
            "id, status, mentee_id, accepted_mentor_id, bounty"
        ).eq("id", request.request_id).single().execute()
        
        if not req_response.data:
            raise HTTPException(status_code=404, detail="Request not found")
        
        req_data = req_response.data
        
        if req_data["accepted_mentor_id"] != user.id:
            raise HTTPException(status_code=403, detail="You are not the accepted mentor for this request")
        
        if req_data["status"] != "accepted":
            raise HTTPException(status_code=400, detail="Request must be accepted before scheduling")
        
        # 2. Check if payment exists (optional - can be enforced)
        # payment_check = supabase.table("payments").select("id, status").eq(
        #     "request_id", request.request_id
        # ).eq("status", "paid").execute()
        # 
        # if not payment_check.data:
        #     raise HTTPException(status_code=400, detail="Payment required before scheduling")
        
        # 3. Create Cal.com booking
        logger.info(f"Creating Cal.com booking for request {request.request_id}")
        
        booking_data = await calcom_service.create_booking(
            event_type_id=request.event_type_id,
            start_time=request.start_time,
            attendee_email=request.mentee_email,
            attendee_name=request.mentee_name,
            metadata={
                "request_id": request.request_id,
                "mentee_id": request.mentee_id,
                "mentor_id": user.id,
                "platform": "avittam"
            }
        )
        
        # Extract meeting URL (Google Meet link from Cal.com)
        meeting_url = booking_data.get("metadata", {}).get("videoCallUrl") or \
                     booking_data.get("location") or \
                     f"https://cal.com/booking/{booking_data.get('uid')}"
        
        # 4. Create session in database
        session_payload = {
            "mentor_id": user.id,
            "mentee_id": request.mentee_id,
            "request_id": request.request_id,
            "scheduled_at": request.start_time.isoformat(),
            "duration_minutes": request.duration_minutes,
            "meeting_url": meeting_url,
            "status": "scheduled",
            "notes": request.notes,
            "cal_com_booking_id": str(booking_data.get("id")),
            "cal_com_uid": booking_data.get("uid"),
        }
        
        session_response = supabase.table("sessions").insert(session_payload).execute()
        
        if not session_response.data:
            raise Exception("Failed to create session in database")
        
        session_id = session_response.data[0]["id"]
        
        # 5. Update request status to 'scheduled' (optional - keep as 'accepted')
        # supabase.table("mentor_requests").update({
        #     "status": "scheduled"
        # }).eq("id", request.request_id).execute()
        
        logger.info(f"Session {session_id} created successfully with Cal.com booking")
        
        return {
            "success": True,
            "data": {
                "session_id": session_id,
                "booking_id": booking_data.get("id"),
                "booking_uid": booking_data.get("uid"),
                "meeting_url": meeting_url,
                "scheduled_at": request.start_time.isoformat(),
            },
            "message": "Session scheduled successfully via Cal.com"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating Cal.com booking: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to schedule session: {str(e)}")


@router.get("/event-types", response_model=ApiResponse)
async def get_mentor_event_types(
    username: str,
    user: User = Depends(get_current_user)
):
    """
    Get available Cal.com event types for a mentor.
    Used to populate dropdown when mentor is scheduling.
    """
    
    if user.role.value != "mentor":
        raise HTTPException(status_code=403, detail="Only mentors can access event types")
    
    try:
        event_types = await calcom_service.get_event_types(username)
        
        return {
            "success": True,
            "data": event_types,
        }
        
    except Exception as e:
        logger.error(f"Error fetching event types: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/cancel-booking/{booking_id}", response_model=ApiResponse)
async def cancel_cal_booking(
    booking_id: int,
    reason: str = "Cancelled by mentor",
    user: User = Depends(get_current_user)
):
    """Cancel a Cal.com booking"""
    
    if user.role.value != "mentor":
        raise HTTPException(status_code=403, detail="Only mentors can cancel bookings")
    
    try:
        success = await calcom_service.cancel_booking(booking_id, reason)
        
        if success:
            # Update session status in database
            supabase = get_supabase_admin()
            supabase.table("sessions").update({
                "status": "cancelled"
            }).eq("cal_com_booking_id", str(booking_id)).execute()
            
            return {
                "success": True,
                "message": "Booking cancelled successfully"
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to cancel booking")
            
    except Exception as e:
        logger.error(f"Error cancelling booking: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
