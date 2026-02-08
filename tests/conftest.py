# =====================================================
# TEST CONFIGURATION
# Pytest fixtures and configuration
# =====================================================

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    """Create a test client for the FastAPI app"""
    return TestClient(app)


@pytest.fixture
def auth_headers():
    """
    Create mock authentication headers.
    In a real test, you'd get a real token from Supabase.
    """
    return {
        "Authorization": "Bearer test-token"
    }
