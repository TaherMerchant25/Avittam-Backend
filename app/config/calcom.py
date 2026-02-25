# =====================================================
# CAL.COM API CONFIGURATION
# =====================================================

from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from loguru import logger


class CalComSettings(BaseSettings):
    """Cal.com API configuration"""
    
    calcom_api_key: str = ""
    calcom_api_url: str = "https://api.cal.com/v1"
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"  # Ignore extra env variables not defined in this class
    )


@lru_cache()
def get_calcom_settings() -> CalComSettings:
    """Get cached Cal.com settings instance"""
    settings = CalComSettings()
    logger.debug("Cal.com settings loaded")
    return settings


# Export settings instance
calcom_settings = get_calcom_settings()
