"""
MENTOR SCHEDULE SESSION - API EXAMPLE
Shows exactly how the mentor-initiated scheduling works
"""

import httpx
import asyncio
from datetime import datetime, timedelta

# =====================================================
# EXAMPLE: Mentor Schedules Session via Cal.com API
# =====================================================

async def schedule_session_example():
    """
    This shows how a mentor schedules a session after accepting a student request.
    The mentor controls WHEN the meeting happens.
    """
    
    # Step 1: Mentor accepts a request (already done in UI)
    request_id = "550e8400-e29b-41d4-a716-446655440000"
    
    # Step 2: Mentor selects date/time in modal
    start_time = datetime.now() + timedelta(days=2)  # 2 days from now
    start_time = start_time.replace(hour=14, minute=0, second=0)  # 2 PM
    
    # Step 3: Mentor's Cal.com settings (from database)
    mentor_calcom_event_type_id = 123456  # From Cal.com dashboard
    
    # Step 4: Student info (from request)
    student_info = {
        "mentee_id": "660e8400-e29b-41d4-a716-446655440001",
        "mentee_email": "student@example.com",
        "mentee_name": "Jane Doe"
    }
    
    # Step 5: Call backend API to create Cal.com booking
    backend_url = "http://localhost:8000/api/calcom/create-booking"
    auth_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."  # Supabase JWT
    
    payload = {
        "request_id": request_id,
        "mentee_id": student_info["mentee_id"],
        "mentee_email": student_info["mentee_email"],
        "mentee_name": student_info["mentee_name"],
        "start_time": start_time.isoformat(),
        "duration_minutes": 60,
        "event_type_id": mentor_calcom_event_type_id,
        "notes": "Career guidance session - Resume review"
    }
    
    headers = {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json"
    }
    
    # Make API call
    async with httpx.AsyncClient() as client:
        response = await client.post(
            backend_url,
            json=payload,
            headers=headers,
            timeout=30.0
        )
        
        if response.status_code == 200:
            data = response.json()
            print("✅ Session scheduled successfully!")
            print(f"Session ID: {data['data']['session_id']}")
            print(f"Meeting URL: {data['data']['meeting_url']}")
            print(f"Scheduled at: {data['data']['scheduled_at']}")
            
            # Step 6: What happens next?
            print("\n📅 What happened:")
            print("1. Cal.com created booking on mentor's Google Calendar")
            print("2. Google Meet link was auto-generated")
            print("3. Session saved to database with meeting URL")
            print("4. Student can now see session in 'My Sessions' page")
            print("5. Both mentor & student can join via meeting link")
            
        else:
            print(f"❌ Error: {response.status_code}")
            print(response.json())


# =====================================================
# WHAT THE BACKEND DOES (python-backend/app/routes/calcom.py)
# =====================================================

def backend_flow_explanation():
    """
    When mentor clicks 'Schedule Session', here's what happens:
    """
    
    # 1. Verify mentor owns the request
    """
    SELECT id, status, accepted_mentor_id 
    FROM mentor_requests 
    WHERE id = request_id
    """
    
    # 2. Check if request is accepted
    """
    if request.status != 'accepted':
        return error "Request must be accepted"
    if request.accepted_mentor_id != current_user_id:
        return error "Not your request"
    """
    
    # 3. Call Cal.com API to create booking
    """
    POST https://api.cal.com/v1/bookings
    {
      "eventTypeId": 123456,
      "start": "2026-02-20T14:00:00.000Z",
      "responses": {
        "name": "Jane Doe",
        "email": "student@example.com",
        "location": {"value": "integrations:google:meet"}
      },
      "timeZone": "Asia/Kolkata"
    }
    """
    
    # 4. Cal.com creates event on mentor's Google Calendar
    """
    Cal.com → Google Calendar API → Event created
    Google Meet link generated automatically
    """
    
    # 5. Save session to database
    """
    INSERT INTO sessions (
      mentor_id,
      mentee_id,
      request_id,
      scheduled_at,
      meeting_url,
      cal_com_booking_id,
      status
    ) VALUES (
      mentor_uuid,
      student_uuid,
      request_uuid,
      '2026-02-20T14:00:00Z',
      'https://meet.google.com/xxx-yyyy-zzz',
      '789',
      'scheduled'
    )
    """
    
    # 6. Return success response
    """
    {
      "success": true,
      "data": {
        "session_id": "uuid",
        "meeting_url": "https://meet.google.com/xxx-yyyy-zzz",
        "scheduled_at": "2026-02-20T14:00:00Z"
      }
    }
    """


# =====================================================
# STUDENT VIEW (pages/StudentDashboard.tsx)
# =====================================================

def student_view_explanation():
    """
    What student sees after mentor schedules:
    """
    
    # Student goes to "My Sessions" tab
    # SessionList component fetches:
    """
    SELECT 
      s.*,
      m.name as mentor_name,
      m.avatar_url as mentor_avatar
    FROM sessions s
    JOIN users m ON s.mentor_id = m.id
    WHERE s.mentee_id = current_user_id
      AND s.status = 'scheduled'
      AND s.scheduled_at > NOW()
    ORDER BY s.scheduled_at ASC
    """
    
    # Student sees:
    """
    ┌───────────────────────────────────────┐
    │ 📅 Upcoming Sessions                  │
    ├───────────────────────────────────────┤
    │                                       │
    │  👤 John Smith (Mentor)              │
    │  📝 Career guidance session           │
    │  📅 Feb 20, 2026 at 2:00 PM          │
    │  ⏱️  60 minutes                       │
    │                                       │
    │  [Join Meeting] ← Click to join      │
    │                                       │
    └───────────────────────────────────────┘
    """
    
    # Click "Join Meeting" opens:
    """
    https://meet.google.com/xxx-yyyy-zzz
    """


# =====================================================
# CURL EXAMPLES FOR TESTING
# =====================================================

def curl_examples():
    """Direct API testing examples"""
    
    # 1. Create booking (mentor-initiated)
    create_booking = """
curl -X POST 'http://localhost:8000/api/calcom/create-booking' \\
  -H 'Authorization: Bearer YOUR_SUPABASE_JWT' \\
  -H 'Content-Type: application/json' \\
  -d '{
    "request_id": "550e8400-e29b-41d4-a716-446655440000",
    "mentee_id": "660e8400-e29b-41d4-a716-446655440001",
    "mentee_email": "student@example.com",
    "mentee_name": "Jane Doe",
    "start_time": "2026-02-20T14:00:00.000Z",
    "duration_minutes": 60,
    "event_type_id": 123456,
    "notes": "Career guidance session"
  }'
    """
    
    # 2. Get mentor's event types
    get_event_types = """
curl -X GET 'http://localhost:8000/api/calcom/event-types?username=john-smith' \\
  -H 'Authorization: Bearer YOUR_SUPABASE_JWT'
    """
    
    # 3. Cancel booking
    cancel_booking = """
curl -X DELETE 'http://localhost:8000/api/calcom/cancel-booking/789' \\
  -H 'Authorization: Bearer YOUR_SUPABASE_JWT' \\
  -H 'Content-Type: application/json' \\
  -d '{"reason": "Student requested cancellation"}'
    """
    
    print("📌 Curl Examples:")
    print(create_booking)
    print(get_event_types)
    print(cancel_booking)


# =====================================================
# COMPARISON: OLD vs NEW FLOW
# =====================================================

def comparison():
    """
    OLD FLOW (Student-initiated Cal.com):
    ❌ Student books directly via Cal.com embed
    ❌ Bypasses request/accept flow
    ❌ No mentor control
    ❌ No payment verification
    
    NEW FLOW (Mentor-initiated Cal.com):
    ✅ Student creates request
    ✅ Mentor accepts request
    ✅ [Optional] Student pays
    ✅ MENTOR schedules via Cal.com API
    ✅ Meeting appears on mentor's calendar
    ✅ Student sees meeting in dashboard
    ✅ Same Gmail creates Cal.com event
    """


if __name__ == "__main__":
    print("=" * 60)
    print("MENTOR-INITIATED CAL.COM SCHEDULING")
    print("=" * 60)
    
    print("\n🔄 Complete Flow:")
    asyncio.run(schedule_session_example())
    
    print("\n" + "=" * 60)
    print("📋 Backend Flow Details:")
    print("=" * 60)
    backend_flow_explanation()
    
    print("\n" + "=" * 60)
    print("👤 Student View:")
    print("=" * 60)
    student_view_explanation()
    
    print("\n" + "=" * 60)
    print("🧪 Testing with Curl:")
    print("=" * 60)
    curl_examples()
