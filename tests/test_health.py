# =====================================================
# HEALTH CHECK TESTS
# =====================================================

def test_root(client):
    """Test root endpoint"""
    response = client.get("/")
    assert response.status_code == 200
    
    data = response.json()
    assert data["success"] is True
    assert data["message"] == "Welcome to MentorGold API"
    assert data["version"] == "1.0.0"


def test_health_check(client):
    """Test health check endpoint"""
    response = client.get("/api/health")
    assert response.status_code == 200
    
    data = response.json()
    assert data["success"] is True
    assert data["message"] == "MentorGold API is running"
    assert "timestamp" in data
    assert data["version"] == "1.0.0"


def test_not_found(client):
    """Test 404 response"""
    response = client.get("/api/nonexistent")
    assert response.status_code == 404
    
    data = response.json()
    assert data["success"] is False
    assert "error" in data
