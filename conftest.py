import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# app.web_server needs LIVEKIT_* present at import time. Provide harmless dummies so
# the suite is green on a clean clone / CI without a local .env.local (setdefault, so
# a real environment still wins). Tests round-trip token metadata, not real creds.
os.environ.setdefault("LIVEKIT_URL", "ws://test.local")
os.environ.setdefault("LIVEKIT_API_KEY", "test-key")
os.environ.setdefault("LIVEKIT_API_SECRET", "test-secret")
