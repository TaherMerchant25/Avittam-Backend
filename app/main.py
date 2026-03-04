# =====================================================
# MENTORGOLD BACKEND API SERVER - FastAPI
# =====================================================

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from loguru import logger
import sys

from app.config.settings import settings
from app.routes import sessions, mentors, meetings, notifications, payments, wallets, calcom, admin, chat
from app.middleware.error_handler import app_exception_handler, AppError


# Configure logging
logger.remove()
logger.add(
    sys.stdout,
    colorize=True,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    level="DEBUG" if settings.debug else "INFO"
)

# Rate limiter
limiter = Limiter(key_func=get_remote_address)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    logger.info("🚀 Starting MentorGold API Server...")
    # Setup Stream Chat bot user on startup
    try:
        from app.services import stream_chat as sc
        if sc.is_stream_chat_configured():
            sc.ensure_system_user()
            sc.ensure_bot_user()
            logger.info("🤖 Stream Chat bot user initialized")
        else:
            logger.warning("⚠️  Stream Chat not configured — add STREAM_CHAT_API_KEY & STREAM_CHAT_API_SECRET to .env")
    except Exception as e:
        logger.warning(f"⚠️  Stream Chat startup init failed: {e}")
    yield
    logger.info("👋 Shutting down MentorGold API Server...")


# Create FastAPI application
app = FastAPI(
    title="MentorGold API",
    description="Backend API for MentorGold - Session booking, Google Meet integration, and mentor management",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Add rate limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Add custom exception handler
app.add_exception_handler(AppError, app_exception_handler)


# =====================================================
# MIDDLEWARE
# =====================================================

# CORS configuration
# Strip trailing slash from frontend_url so it matches the Origin header browsers send
_frontend = settings.frontend_url.rstrip("/")
allowed_origins = [
    _frontend,
    f"{_frontend}/",          # also allow with trailing slash just in case
    "https://avittam.vercel.app",
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
    expose_headers=["Content-Length"],
)


# Request logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger.info(f"📨 {request.method} {request.url.path}")
    response = await call_next(request)
    logger.info(f"📤 {request.method} {request.url.path} - {response.status_code}")
    return response


# =====================================================
# ROUTES
# =====================================================

# Root endpoint
@app.get("/", tags=["Health"])
async def root():
    return {
        "success": True,
        "message": "Welcome to MentorGold API",
        "version": "1.0.0",
        "documentation": "/docs",
    }


# Health check
@app.get("/api/health", tags=["Health"])
async def health_check():
    return {
        "success": True,
        "message": "MentorGold API is running",
        "timestamp": __import__("datetime").datetime.now().isoformat(),
        "version": "1.0.0",
    }


# Mount route modules
app.include_router(sessions.router, prefix="/api/sessions", tags=["Sessions"])
app.include_router(mentors.router, prefix="/api/mentors", tags=["Mentors"])
app.include_router(meetings.router, prefix="/api/meetings", tags=["Meetings"])
app.include_router(notifications.router, prefix="/api/notifications", tags=["Notifications"])
app.include_router(payments.router, prefix="/api/payments", tags=["Payments"])
app.include_router(wallets.router, prefix="/api/wallets", tags=["Wallets"])
app.include_router(calcom.router, prefix="/api/calcom", tags=["Cal.com"])
app.include_router(admin.router, prefix="/api/admin", tags=["Admin"])
app.include_router(chat.router, prefix="/api/chat", tags=["Chat"])


# =====================================================
# ERROR HANDLING
# =====================================================

@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return JSONResponse(
        status_code=404,
        content={
            "success": False,
            "error": f"Resource not found: {request.url.path}",
        }
    )


@app.exception_handler(500)
async def internal_error_handler(request: Request, exc):
    logger.error(f"Internal error: {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": "Internal server error",
        }
    )


# =====================================================
# RUN SERVER
# =====================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.port,
        reload=settings.debug,
    )
