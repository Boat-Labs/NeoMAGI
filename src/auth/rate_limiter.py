"""In-memory login rate limiter (P2-M3a)."""

from __future__ import annotations

import time


class LoginRateLimiter:
    """Simple IP-based rate limiter for login attempts.

    Not persistent — resets on restart (acceptable for single-user harness).
    """

    def __init__(
        self,
        max_failures: int = 5,
        window_seconds: int = 60,
        lockout_seconds: int = 300,
    ) -> None:
        self._max_failures = max_failures
        self._window_seconds = window_seconds
        self._lockout_seconds = lockout_seconds
        self._state: dict[str, tuple[int, float]] = {}  # ip -> (fail_count, window_start)

    def is_locked(self, ip: str) -> bool:
        """Check if IP is currently locked out."""
        entry = self._state.get(ip)
        if entry is None:
            return False
        fail_count, window_start = entry
        now = time.monotonic()
        if fail_count >= self._max_failures:
            if now - window_start < self._lockout_seconds:
                return True
            # Lockout expired — reset
            del self._state[ip]
            return False
        # Window expired without hitting threshold — reset
        if now - window_start >= self._window_seconds:
            del self._state[ip]
            return False
        return False

    def record_failure(self, ip: str) -> None:
        """Record a failed login attempt."""
        now = time.monotonic()
        entry = self._state.get(ip)
        if entry is None or now - entry[1] >= self._window_seconds:
            self._state[ip] = (1, now)
        else:
            self._state[ip] = (entry[0] + 1, entry[1])

    def record_success(self, ip: str) -> None:
        """Clear failure count on successful login."""
        self._state.pop(ip, None)
