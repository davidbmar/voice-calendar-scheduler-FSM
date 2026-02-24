# Ensure engine-repo is on sys.path so engine.* and voice_assistant.* imports
# work without per-file sys.path hacks. PYTHONPATH in scripts handles the
# primary case; this covers programmatic imports within the scheduling package.
import os
import sys

_engine_repo = os.path.join(os.path.dirname(__file__), "..", "engine-repo")
if _engine_repo not in sys.path:
    sys.path.insert(0, _engine_repo)
