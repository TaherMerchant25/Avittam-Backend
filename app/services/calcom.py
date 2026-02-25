# =====================================================
# CAL.COM API SERVICE
# Handles mentor-initiated booking creation via Cal.com API
# =====================================================

import httpx
from typing import Dict, Any, Optional
from datetime import datetime
from loguru import logger

from app.config.calcom import calcom_settings


class CalComService:
    """Cal.com API integration for mentor-initiated scheduling"""
    
    def __init__(self):
        self.api_key = calcom_settings.calcom_api_key
        self.api_url = calcom_settings.calcom_api_url
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
    
    async def create_booking(
        self,
        event_type_id: int,
        start_time: datetime,
        attendee_email: str,
        attendee_name: str,
        attendee_timezone: str = "Asia/Kolkata",
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Create a booking on mentor's Cal.com calendar.
        
        Args:
            event_type_id: Cal.com event type ID (from mentor's profile)
            start_time: Meeting start time (ISO 8601 format)
            attendee_email: Student's email
            attendee_name: Student's name
            attendee_timezone: Student's timezone
            metadata: Additional metadata (session_id, request_id, etc.)
            
        Returns:
            Dict with booking details including meeting URL
        """
        if not self.api_key:
            raise ValueError("Cal.com API key not configured")
        
        payload = {
            "eventTypeId": event_type_id,
            "start": start_time.isoformat(),
            "responses": {
                "name": attendee_name,
                "email": attendee_email,
                "location": {"value": "integrations:google:meet", "optionValue": ""},
            },
            "timeZone": attendee_timezone,
            "language": "en",
            "metadata": metadata or {}
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_url}/bookings",
                    json=payload,
                    headers=self.headers,
                    timeout=30.0
                )
                response.raise_for_status()
                booking_data = response.json()
                
                logger.info(f"Cal.com booking created: {booking_data.get('id')}")
                return booking_data
                
        except httpx.HTTPStatusError as e:
            logger.error(f"Cal.com API error: {e.response.status_code} - {e.response.text}")
            raise Exception(f"Failed to create Cal.com booking: {e.response.text}")
        except Exception as e:
            logger.error(f"Unexpected error creating Cal.com booking: {str(e)}")
            raise
    
    async def get_event_types(self, username: str) -> list[Dict[str, Any]]:
        """
        Get available event types for a Cal.com user.
        
        Args:
            username: Cal.com username
            
        Returns:
            List of event types
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.api_url}/event-types",
                    headers=self.headers,
                    params={"username": username},
                    timeout=30.0
                )
                response.raise_for_status()
                return response.json().get("event_types", [])
                
        except Exception as e:
            logger.error(f"Error fetching Cal.com event types: {str(e)}")
            return []
    
    async def cancel_booking(self, booking_id: int, reason: str = "Cancelled by mentor") -> bool:
        """
        Cancel a Cal.com booking.
        
        Args:
            booking_id: Cal.com booking ID
            reason: Cancellation reason
            
        Returns:
            True if successful
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.delete(
                    f"{self.api_url}/bookings/{booking_id}",
                    headers=self.headers,
                    json={"reason": reason},
                    timeout=30.0
                )
                response.raise_for_status()
                logger.info(f"Cal.com booking {booking_id} cancelled")
                return True
                
        except Exception as e:
            logger.error(f"Error cancelling Cal.com booking: {str(e)}")
            return False


# Singleton instance
calcom_service = CalComService()
