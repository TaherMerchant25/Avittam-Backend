# =====================================================
# UTILITY FUNCTIONS
# Helper functions used across the application
# =====================================================

from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import math


def paginate(
    page: int = 1,
    limit: int = 20,
    total: int = 0
) -> Dict[str, int]:
    """
    Calculate pagination metadata.
    
    Args:
        page: Current page number (1-indexed)
        limit: Items per page
        total: Total number of items
        
    Returns:
        Pagination metadata dictionary
    """
    total_pages = math.ceil(total / limit) if limit > 0 else 0
    
    return {
        "page": page,
        "limit": limit,
        "total": total,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1,
    }


def calculate_offset(page: int, limit: int) -> int:
    """Calculate database offset from page and limit"""
    return (page - 1) * limit


def format_datetime(dt: Optional[datetime], format_str: str = "%Y-%m-%d %H:%M") -> Optional[str]:
    """Format datetime to string"""
    if dt is None:
        return None
    return dt.strftime(format_str)


def parse_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    """Parse datetime string to datetime object"""
    if dt_str is None:
        return None
    
    # Try different formats
    formats = [
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%S.%f+00:00",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            continue
    
    return None


def is_expired(expires_at: datetime) -> bool:
    """Check if a datetime has passed"""
    return datetime.now() > expires_at


def get_expires_in(expires_at: datetime) -> Optional[int]:
    """Get seconds until expiration (negative if expired)"""
    delta = expires_at - datetime.now()
    return int(delta.total_seconds())


def add_hours(dt: datetime, hours: int) -> datetime:
    """Add hours to a datetime"""
    return dt + timedelta(hours=hours)


def add_minutes(dt: datetime, minutes: int) -> datetime:
    """Add minutes to a datetime"""
    return dt + timedelta(minutes=minutes)


def sanitize_string(value: Optional[str], max_length: int = 1000) -> Optional[str]:
    """Sanitize and truncate a string"""
    if value is None:
        return None
    
    # Strip whitespace
    value = value.strip()
    
    # Truncate if too long
    if len(value) > max_length:
        value = value[:max_length]
    
    return value


def mask_email(email: str) -> str:
    """Mask email for privacy (e.g., t***r@gmail.com)"""
    if "@" not in email:
        return email
    
    local, domain = email.split("@", 1)
    
    if len(local) <= 2:
        masked_local = local[0] + "*"
    else:
        masked_local = local[0] + "*" * (len(local) - 2) + local[-1]
    
    return f"{masked_local}@{domain}"


def generate_session_title(mentor_name: str, mentee_name: str) -> str:
    """Generate a default session title"""
    return f"MentorGold Session: {mentor_name} & {mentee_name}"


def calculate_session_end_time(start_time: datetime, duration_minutes: int) -> datetime:
    """Calculate session end time from start time and duration"""
    return start_time + timedelta(minutes=duration_minutes)
