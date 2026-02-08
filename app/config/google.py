# =====================================================
# GOOGLE API CONFIGURATION
# OAuth2 setup for Google Calendar and Meet
# =====================================================

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from typing import Optional, Dict, Any
from loguru import logger

from app.config.settings import settings


# OAuth2 scopes required for Google Meet and Calendar
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]


def get_oauth2_flow() -> Flow:
    """
    Create OAuth2 flow for user authentication.
    
    Returns:
        Google OAuth2 Flow instance
    """
    if not settings.google_client_id or not settings.google_client_secret:
        raise ValueError("Google OAuth credentials not configured")
    
    client_config = {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uris": [settings.google_redirect_uri],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    
    flow = Flow.from_client_config(
        client_config,
        scopes=GOOGLE_SCOPES,
        redirect_uri=settings.google_redirect_uri,
    )
    
    return flow


def get_google_auth_url(state: Optional[str] = None) -> str:
    """
    Generate OAuth URL for user consent.
    
    Args:
        state: Optional state parameter for callback
        
    Returns:
        Google OAuth authorization URL
    """
    flow = get_oauth2_flow()
    
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    
    return auth_url


async def get_google_tokens(code: str) -> Dict[str, Any]:
    """
    Exchange authorization code for tokens.
    
    Args:
        code: Authorization code from OAuth callback
        
    Returns:
        Dictionary with access_token, refresh_token, etc.
    """
    flow = get_oauth2_flow()
    flow.fetch_token(code=code)
    
    credentials = flow.credentials
    
    return {
        "access_token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "expiry_date": credentials.expiry.timestamp() * 1000 if credentials.expiry else None,
        "token_type": "Bearer",
        "scope": " ".join(credentials.scopes) if credentials.scopes else "",
    }


def get_calendar_client(access_token: str, refresh_token: Optional[str] = None):
    """
    Get Google Calendar API client with user's credentials.
    
    Args:
        access_token: User's access token
        refresh_token: Optional refresh token
        
    Returns:
        Google Calendar API client
    """
    credentials = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        token_uri="https://oauth2.googleapis.com/token",
    )
    
    return build("calendar", "v3", credentials=credentials)


async def verify_google_token(token: str) -> Optional[Dict[str, Any]]:
    """
    Verify a Google access token.
    
    Args:
        token: Google access token
        
    Returns:
        Token info if valid, None otherwise
    """
    import httpx
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://www.googleapis.com/oauth2/v3/tokeninfo?access_token={token}"
            )
            if response.status_code == 200:
                return response.json()
    except Exception as e:
        logger.error(f"Error verifying Google token: {e}")
    
    return None
