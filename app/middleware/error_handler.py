# =====================================================
# ERROR HANDLING MIDDLEWARE
# Custom exceptions and error handlers
# =====================================================

from fastapi import Request
from fastapi.responses import JSONResponse
from typing import Dict, Any, Optional
from loguru import logger


class AppError(Exception):
    """Base application error class"""
    
    def __init__(
        self,
        message: str = "An error occurred",
        status_code: int = 500,
        errors: Optional[Dict[str, Any]] = None
    ):
        self.message = message
        self.status_code = status_code
        self.errors = errors or {}
        super().__init__(self.message)


class BadRequestError(AppError):
    """400 Bad Request"""
    def __init__(self, message: str = "Bad request"):
        super().__init__(message, 400)


class UnauthorizedError(AppError):
    """401 Unauthorized"""
    def __init__(self, message: str = "Unauthorized"):
        super().__init__(message, 401)


class ForbiddenError(AppError):
    """403 Forbidden"""
    def __init__(self, message: str = "Forbidden"):
        super().__init__(message, 403)


class NotFoundError(AppError):
    """404 Not Found"""
    def __init__(self, message: str = "Resource not found"):
        super().__init__(message, 404)


class ConflictError(AppError):
    """409 Conflict"""
    def __init__(self, message: str = "Resource conflict"):
        super().__init__(message, 409)


class ValidationError(AppError):
    """422 Validation Error"""
    def __init__(
        self,
        message: str = "Validation failed",
        errors: Optional[Dict[str, Any]] = None
    ):
        super().__init__(message, 422, errors)


class RateLimitError(AppError):
    """429 Too Many Requests"""
    def __init__(self, message: str = "Too many requests, please try again later"):
        super().__init__(message, 429)


async def app_exception_handler(request: Request, exc: AppError) -> JSONResponse:
    """Global exception handler for AppError"""
    
    logger.error(
        f"Error: {exc.__class__.__name__} - {exc.message} "
        f"| Path: {request.url.path} | Method: {request.method}"
    )
    
    response_content = {
        "success": False,
        "error": exc.message,
    }
    
    if exc.errors:
        response_content["errors"] = exc.errors
    
    return JSONResponse(
        status_code=exc.status_code,
        content=response_content
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handler for unexpected exceptions"""
    
    logger.exception(f"Unexpected error: {exc}")
    
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": "Internal server error",
        }
    )
