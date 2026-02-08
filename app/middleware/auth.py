# =====================================================
# AUTHENTICATION MIDDLEWARE
# JWT verification and user injection
# =====================================================

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from typing import Optional
from loguru import logger

from app.config.settings import settings
from app.config.database import get_supabase_admin
from app.models.schemas import User, UserRole
from app.middleware.error_handler import UnauthorizedError


security = HTTPBearer()


async def verify_token(token: str) -> Optional[dict]:
    """
    Verify JWT token and return payload.
    Tries Supabase token first, then custom JWT.
    """
    supabase = get_supabase_admin()
    
    # Try to verify as Supabase JWT
    try:
        user_response = supabase.auth.get_user(token)
        if user_response and user_response.user:
            return {
                "sub": user_response.user.id,
                "email": user_response.user.email,
            }
    except Exception as e:
        logger.debug(f"Supabase token verification failed: {e}")
    
    # Fallback: verify as custom JWT
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm]
        )
        return payload
    except JWTError as e:
        logger.debug(f"JWT verification failed: {e}")
        raise UnauthorizedError("Invalid or expired token")


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> User:
    """
    FastAPI dependency to get current authenticated user.
    
    Usage:
        @router.get("/protected")
        async def protected_route(user: User = Depends(get_current_user)):
            return {"user": user}
    """
    token = credentials.credentials
    
    try:
        payload = await verify_token(token)
        
        if not payload or "sub" not in payload:
            raise UnauthorizedError("Invalid token payload")
        
        user_id = payload["sub"]
        
        # Fetch user from database
        supabase = get_supabase_admin()
        result = supabase.table("users").select("*").eq("id", user_id).single().execute()
        
        if not result.data:
            raise UnauthorizedError("User not found")
        
        user_data = result.data
        
        return User(
            id=user_data["id"],
            email=user_data["email"],
            name=user_data["name"],
            role=UserRole(user_data["role"]),
            avatar_url=user_data.get("avatar_url"),
            is_verified=user_data.get("is_verified", False),
            is_active=user_data.get("is_active", True),
            created_at=user_data["created_at"],
            updated_at=user_data["updated_at"],
        )
        
    except UnauthorizedError:
        raise
    except Exception as e:
        logger.error(f"Authentication error: {e}")
        raise UnauthorizedError("Authentication failed")


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(
        HTTPBearer(auto_error=False)
    )
) -> Optional[User]:
    """
    FastAPI dependency to optionally get current user.
    Returns None if no valid token is provided.
    """
    if not credentials:
        return None
    
    try:
        return await get_current_user(credentials)
    except Exception:
        return None


def require_role(*roles: UserRole):
    """
    Dependency factory to require specific user roles.
    
    Usage:
        @router.get("/admin-only")
        async def admin_route(user: User = Depends(require_role(UserRole.ADMIN))):
            return {"message": "Admin access granted"}
    """
    async def role_checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Required roles: {[r.value for r in roles]}"
            )
        return user
    
    return role_checker


def require_mentor(user: User = Depends(get_current_user)) -> User:
    """Dependency to require mentor role"""
    if user.role not in [UserRole.MENTOR, UserRole.ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Mentor access required"
        )
    return user


def require_mentee(user: User = Depends(get_current_user)) -> User:
    """Dependency to require mentee role"""
    if user.role not in [UserRole.MENTEE, UserRole.ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Mentee access required"
        )
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Dependency to require admin role"""
    if user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return user
