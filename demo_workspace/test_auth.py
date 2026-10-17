from auth import is_token_expired

def test_valid_token_not_expired():
    token = {"expires_at": 2000}
    assert is_token_expired(token, current_time=1500) is False

def test_expired_token_detected():
    token = {"expires_at": 2000}
    assert is_token_expired(token, current_time=2500) is True

def test_exact_boundary():
    token = {"expires_at": 2000}
    assert is_token_expired(token, current_time=2000) is True
