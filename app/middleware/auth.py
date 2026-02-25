# =====================================================
# AUTHENTICATION MIDDLEWARE
# JWT verification and user injection
# =====================================================

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt, jwk
from jose.backends import ECKey
from typing import Optional
from loguru import logger
import httpx
from functools import lru_cache

from app.config.settings import settings
from app.models.schemas import User, UserRole
from app.middleware.error_handler import UnauthorizedError


security = HTTPBearer()


@lru_cache(maxsize=1)
def get_jwks():
    """Fetch JWKS from Supabase (cached)"""
    jwks_url = f"{settings.supabase_url}/auth/v1/.well-known/jwks.json"
    try:
        response = httpx.get(jwks_url, timeout=10.0)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Failed to fetch JWKS: {e}")
        return None


async def verify_token(token: str) -> Optional[dict]:
    """
    Verify JWT token and return payload.
    Tries Supabase token (ES256 with JWKS) first, then custom JWT.
    """
    # Get the key ID from token header
    try:
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")
        alg = unverified_header.get("alg", "ES256")
        
        # Try to verify as Supabase JWT using JWKS
        jwks_data = get_jwks()
        if jwks_data and kid:
            # Find the matching key
            keys = jwks_data.get("keys", [])
            matching_key = None
            for key in keys:
                if key.get("kid") == kid:
                    matching_key = key
                    break
            
            if matching_key:
                # Verify token with the public key
                payload = jwt.decode(
                    token,
                    matching_key,
                    algorithms=[alg],
                    audience="authenticated",
                    options={"verify_aud": True}
                )
                logger.debug(f"Supabase token verified for user: {payload.get('sub')}")
                return payload
    except JWTError as e:
        logger.debug(f"Supabase JWKS token verification failed: {e}")
    except Exception as e:
        logger.debug(f"Token verification error: {e}")
    
    # Fallback: try with JWT secret if configured (HS256)
    if settings.supabase_jwt_secret:
        try:
            payload = jwt.decode(
                token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                audience="authenticated"
            )
            logger.debug(f"Supabase HS256 token verified for user: {payload.get('sub')}")
            return payload
        except JWTError as e:
            logger.debug(f"Supabase HS256 token verification failed: {e}")
    
    # Fallback: validate via Supabase Auth API (no JWT secret needed)
    # Try with anon key first, then service_role (some projects require service_role for server-side)
    for apikey in [settings.supabase_anon_key, settings.supabase_service_role_key or ""]:
        if not apikey:
            continue
        try:
            url = f"{settings.supabase_url.rstrip('/')}/auth/v1/user"
            response = httpx.get(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "apikey": apikey,
                    "Content-Type": "application/json",
                },
                timeout=10.0,
            )
            if response.status_code == 200:
                data = response.json()
                user_id = None
                user_email = None
                if isinstance(data, dict):
                    user_id = data.get("id") or (data.get("user") or {}).get("id") or data.get("sub")
                    user_email = data.get("email") or (data.get("user") or {}).get("email")
                if user_id:
                    logger.info(f"Supabase API token verified for user: {user_id}")
                    return {"sub": user_id, "email": user_email}
            else:
                logger.warning(f"Supabase API auth failed: {response.status_code} - {response.text[:400]}")
        except Exception as e:
            logger.debug(f"Supabase API attempt failed: {e}")

    # Last fallback: verify as custom JWT
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm]
        )
        logger.debug(f"Custom JWT verified for user: {payload.get('sub')}")
        return payload
    except JWTError as e:
        logger.debug(f"Custom JWT verification failed: {e}")
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
        
        # Fetch user from database via Supabase REST API (avoids supabase-py Client proxy issue)
        url = f"{settings.supabase_url.rstrip('/')}/rest/v1/users"
        async with httpx.AsyncClient() as client:
            response = await client.get(
                url,
                params={"id": f"eq.{user_id}", "select": "*"},
                headers={
                    "apikey": settings.supabase_service_role_key or settings.supabase_anon_key,
                    "Authorization": f"Bearer {settings.supabase_service_role_key or settings.supabase_anon_key}",
                    "Accept": "application/json",
                },
                timeout=10.0,
            )
        if response.status_code != 200:
            logger.warning(f"Supabase REST users fetch failed: {response.status_code} - {response.text[:200]}")
            raise UnauthorizedError("User not found")
        data = response.json()
        user_data = data[0] if isinstance(data, list) and data else None
        if not user_data:
            raise UnauthorizedError("User not found")
        
        return User(
            id=str(user_data["id"]),
            email=user_data["email"],
            name=user_data.get("name") or "",
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
