"""Force hermetic tests: never touch a real database.

The developer's .env may point at production Atlas. Tests must always run
against the in-memory fallback, so override the URI before app import and
clear the cached settings.
"""

import os

os.environ["MONGODB_URI"] = "mongodb://127.0.0.1:9/unreachable-test-only"

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()
