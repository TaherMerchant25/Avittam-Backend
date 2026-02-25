# =====================================================
# APPLICATION SETTINGS
# Environment configuration with Pydantic Settings
# =====================================================

from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache
from typing import Optional

# Resolve .env path: settings.py is in app/config/, .env is in python-backend/
_ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
# Pre-load .env into os.environ so Pydantic picks it up (handles cwd-independent loading)
if _ENV_PATH.exists():
    from dotenv import load_dotenv
    load_dotenv(_ENV_PATH, override=False)


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""
    
    # Server Configuration
    port: int = Field(default=8000, alias="PORT")
    debug: bool = Field(default=True, alias="DEBUG")
    environment: str = Field(default="development", alias="ENVIRONMENT")
    
    # Frontend URL
    frontend_url: str = Field(default="http://localhost:3000", alias="FRONTEND_URL")
    
    # Supabase Configuration
    supabase_url: str = Field(..., alias="SUPABASE_URL")
    supabase_anon_key: str = Field(..., alias="SUPABASE_ANON_KEY")
    supabase_service_role_key: Optional[str] = Field(default=None, alias="SUPABASE_SERVICE_ROLE_KEY")
    supabase_jwt_secret: Optional[str] = Field(default=None, alias="SUPABASE_JWT_SECRET")
    
    # JWT Configuration
    jwt_secret: str = Field(default="your-secret-key", alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_expiration_hours: int = Field(default=24, alias="JWT_EXPIRATION_HOURS")
    
    # Google OAuth Configuration
    google_client_id: Optional[str] = Field(default=None, alias="GOOGLE_CLIENT_ID")
    google_client_secret: Optional[str] = Field(default=None, alias="GOOGLE_CLIENT_SECRET")
    google_redirect_uri: str = Field(
        default="http://localhost:8000/api/meetings/google/callback",
        alias="GOOGLE_REDIRECT_URI"
    )
    
    # Rate Limiting
    rate_limit_per_minute: int = Field(default=60, alias="RATE_LIMIT_PER_MINUTE")
    
    # Razorpay Configuration
    razorpay_key_id: Optional[str] = Field(default=None, alias="RAZORPAY_KEY_ID")
    razorpay_key_secret: Optional[str] = Field(default=None, alias="RAZORPAY_KEY_SECRET")

    # Stream Chat
    stream_chat_api_key: Optional[str] = Field(default=None, alias="STREAM_CHAT_API_KEY")
    stream_chat_api_secret: Optional[str] = Field(default=None, alias="STREAM_CHAT_API_SECRET")
    
    class Config:
        env_file = str(_ENV_PATH)
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()


# Global settings instance
settings = get_settings()
