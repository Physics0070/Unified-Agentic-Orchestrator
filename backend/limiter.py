"""The shared slowapi limiter, in its own module so routers can decorate with it
without importing backend.main (which imports them)."""

from slowapi import Limiter
from slowapi.util import get_remote_address

from backend.config import settings

limiter = Limiter(key_func=get_remote_address, default_limits=[settings.rate_limit_public])
