# =====================================================
# SUPABASE DATABASE CONFIGURATION
# =====================================================

from supabase import create_client, Client
from functools import lru_cache
from loguru import logger

from app.config.settings import settings


@lru_cache()
def get_supabase_admin() -> Client:
    """
    Get Supabase admin client (bypasses RLS).
    Uses service role key for admin operations.
    """
    if not settings.supabase_service_role_key:
        raise ValueError("SUPABASE_SERVICE_ROLE_KEY is not configured")
    
    client = create_client(
        settings.supabase_url,
        settings.supabase_service_role_key
    )
    logger.debug("Supabase admin client initialized")
    return client


@lru_cache()
def get_supabase_client() -> Client:
    """
    Get Supabase anon client (respects RLS).
    Uses anon key for user-scoped operations.
    """
    client = create_client(
        settings.supabase_url,
        settings.supabase_anon_key
    )
    logger.debug("Supabase client initialized")
    return client


def get_supabase_with_token(user_token: str) -> Client:
    """
    Create a Supabase client with user's JWT for RLS.
    
    Args:
        user_token: User's JWT token
        
    Returns:
        Supabase client configured with user token
    """
    from supabase import ClientOptions
    
    options = ClientOptions()
    options.headers = {"Authorization": f"Bearer {user_token}"}
    
    client = create_client(
        settings.supabase_url,
        settings.supabase_anon_key,
        options
    )
    return client


# Dependency for FastAPI
async def get_db() -> Client:
    """FastAPI dependency for database access"""
    return get_supabase_admin()
