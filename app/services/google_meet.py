# =====================================================
# GOOGLE MEET SERVICE
# Create and manage Google Meet video conferences
# =====================================================

from datetime import datetime
from typing import Optional, Dict, Any, List
from uuid import uuid4
from loguru import logger

from app.config.database import get_supabase_admin
from app.config.google import get_calendar_client
from app.models.schemas import CalendarEventInput, GoogleMeetDetails


async def get_user_google_tokens(user_id: str) -> Optional[Dict[str, Any]]:
    """Get user's stored Google tokens from database"""
    supabase = get_supabase_admin()
    
    result = supabase.table("user_google_tokens").select("*").eq(
        "user_id", user_id
    ).single().execute()
    
    if not result.data:
        logger.debug(f"No Google tokens found for user {user_id}")
        return None
    
    return result.data


async def store_user_google_tokens(
    user_id: str,
    tokens: Dict[str, Any]
) -> None:
    """Store user's Google tokens in database"""
    supabase = get_supabase_admin()
    
    token_data = {
        "user_id": user_id,
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token"),
        "expiry_date": tokens.get("expiry_date"),
        "updated_at": datetime.now().isoformat(),
    }
    
    result = supabase.table("user_google_tokens").upsert(
        token_data,
        on_conflict="user_id"
    ).execute()
    
    if not result.data:
        logger.error("Failed to store Google tokens")
        raise Exception("Failed to store Google tokens")


async def create_google_meet_session(
    host_user_id: str,
    event_input: Dict[str, Any]
) -> Dict[str, Any]:
    """Create a Google Calendar event with Google Meet video conferencing"""
    
    # Get host's Google tokens
    tokens = await get_user_google_tokens(host_user_id)
    
    if not tokens or not tokens.get("access_token"):
        raise Exception("Host has not connected their Google account. Please authorize Google Calendar access.")
    
    calendar = get_calendar_client(tokens["access_token"], tokens.get("refresh_token"))
    
    # Create unique conference request ID
    conference_request_id = str(uuid4())
    
    # Build calendar event with Google Meet
    event = {
        "summary": event_input["summary"],
        "description": event_input.get("description", "MentorGold Mentorship Session"),
        "start": {
            "dateTime": event_input["start_time"],
            "timeZone": event_input.get("timezone", "Asia/Kolkata"),
        },
        "end": {
            "dateTime": event_input["end_time"],
            "timeZone": event_input.get("timezone", "Asia/Kolkata"),
        },
        "attendees": [
            {"email": a["email"], "displayName": a.get("name"), "responseStatus": "needsAction"}
            for a in event_input.get("attendees", [])
        ],
        "conferenceData": {
            "createRequest": {
                "requestId": conference_request_id,
                "conferenceSolutionKey": {
                    "type": "hangoutsMeet",
                },
            },
        },
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "email", "minutes": 60},
                {"method": "popup", "minutes": 15},
            ],
        },
        "guestsCanModify": False,
        "guestsCanInviteOthers": False,
    }
    
    try:
        response = calendar.events().insert(
            calendarId="primary",
            body=event,
            conferenceDataVersion=1,
            sendUpdates="all",
        ).execute()
        
        if not response.get("conferenceData", {}).get("entryPoints"):
            raise Exception("Failed to create Google Meet conference")
        
        meet_link = next(
            (ep for ep in response["conferenceData"]["entryPoints"] if ep["entryPointType"] == "video"),
            None
        )
        
        if not meet_link or not meet_link.get("uri"):
            raise Exception("No Google Meet link in created event")
        
        return {
            "meeting_url": meet_link["uri"],
            "meeting_id": response["conferenceData"].get("conferenceId", conference_request_id),
            "calendar_event_id": response.get("id", ""),
            "conference_data": {
                "conference_id": response["conferenceData"].get("conferenceId", ""),
                "conference_solution": response["conferenceData"].get("conferenceSolution", {}).get("name", "Google Meet"),
                "entry_points": [
                    {
                        "entry_point_type": ep.get("entryPointType", ""),
                        "uri": ep.get("uri", ""),
                        "label": ep.get("label"),
                    }
                    for ep in response["conferenceData"]["entryPoints"]
                ],
            },
        }
        
    except Exception as e:
        logger.error(f"Error creating Google Meet session: {e}")
        raise


async def update_google_meet_session(
    host_user_id: str,
    calendar_event_id: str,
    updates: Dict[str, Any]
) -> None:
    """Update an existing Google Calendar event"""
    
    tokens = await get_user_google_tokens(host_user_id)
    
    if not tokens or not tokens.get("access_token"):
        raise Exception("Host has not connected their Google account")
    
    calendar = get_calendar_client(tokens["access_token"], tokens.get("refresh_token"))
    
    update_data = {}
    
    if updates.get("summary"):
        update_data["summary"] = updates["summary"]
    if updates.get("description"):
        update_data["description"] = updates["description"]
    if updates.get("start_time"):
        update_data["start"] = {
            "dateTime": updates["start_time"],
            "timeZone": updates.get("timezone", "Asia/Kolkata"),
        }
    if updates.get("end_time"):
        update_data["end"] = {
            "dateTime": updates["end_time"],
            "timeZone": updates.get("timezone", "Asia/Kolkata"),
        }
    if updates.get("attendees"):
        update_data["attendees"] = [
            {"email": a["email"], "displayName": a.get("name")}
            for a in updates["attendees"]
        ]
    
    calendar.events().patch(
        calendarId="primary",
        eventId=calendar_event_id,
        body=update_data,
        sendUpdates="all",
    ).execute()


async def cancel_google_meet_session(
    host_user_id: str,
    calendar_event_id: str
) -> None:
    """Cancel/delete a Google Calendar event"""
    
    tokens = await get_user_google_tokens(host_user_id)
    
    if not tokens or not tokens.get("access_token"):
        raise Exception("Host has not connected their Google account")
    
    calendar = get_calendar_client(tokens["access_token"], tokens.get("refresh_token"))
    
    calendar.events().delete(
        calendarId="primary",
        eventId=calendar_event_id,
        sendUpdates="all",
    ).execute()


async def get_user_calendar_events(
    user_id: str,
    max_results: int = 10
) -> List[Dict[str, Any]]:
    """Get upcoming events from user's calendar"""
    
    tokens = await get_user_google_tokens(user_id)
    
    if not tokens or not tokens.get("access_token"):
        raise Exception("User has not connected their Google account")
    
    calendar = get_calendar_client(tokens["access_token"], tokens.get("refresh_token"))
    
    response = calendar.events().list(
        calendarId="primary",
        timeMin=datetime.now().isoformat() + "Z",
        maxResults=max_results,
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    
    return response.get("items", [])


async def check_calendar_availability(
    user_id: str,
    start_time: str,
    end_time: str
) -> bool:
    """Check if a time slot is available in user's calendar"""
    
    tokens = await get_user_google_tokens(user_id)
    
    if not tokens or not tokens.get("access_token"):
        raise Exception("User has not connected their Google account")
    
    calendar = get_calendar_client(tokens["access_token"], tokens.get("refresh_token"))
    
    # Query for events in the time range
    response = calendar.events().list(
        calendarId="primary",
        timeMin=start_time,
        timeMax=end_time,
        singleEvents=True,
    ).execute()
    
    events = response.get("items", [])
    
    # If no events, the slot is available
    return len(events) == 0
