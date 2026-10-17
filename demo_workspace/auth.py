import time

def is_token_expired(token: dict, current_time: int = None) -> bool:
    if current_time is None:
        current_time = int(time.time())
    expires_at = token.get("expires_at", 0)
    return current_time >= expires_at
