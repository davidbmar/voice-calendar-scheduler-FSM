# Ensure engine-repo is on sys.path so engine.* and voice_assistant.* imports
# work without per-file sys.path hacks. PYTHONPATH in scripts handles the
# primary case; this covers programmatic imports within the scheduling package.
#
# IMPORTANT: append (not insert at 0) so that the project root's gateway/
# package is found before engine-repo/gateway/ — both dirs have a gateway/
# and insert(0) would shadow the project's own gateway.server module.
import os
import sys

_engine_repo = os.path.join(os.path.dirname(__file__), "..", "engine-repo")
if _engine_repo not in sys.path:
    sys.path.append(_engine_repo)
