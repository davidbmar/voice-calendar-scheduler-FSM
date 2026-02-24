"""WebRTC Session — re-exports engine's Session with scheduling-app config.

This module imports the Session class from the engine submodule and
re-exports it.  When creating a Session, callers should pass ICE servers
obtained from gateway.turn.fetch_twilio_turn_credentials() or from the
fallback ice_servers_json in scheduling.config.settings.

The engine's Session class (engine-repo/gateway/webrtc.py) handles:
  - RTCPeerConnection lifecycle
  - SDP offer/answer exchange
  - Mic audio capture (48kHz PCM)
  - TTS audio playback via an AudioQueue

Usage::

    from gateway.turn import fetch_twilio_turn_credentials
    from gateway.webrtc import Session

    ice_servers = await fetch_twilio_turn_credentials()
    session = Session(ice_servers=ice_servers)
    answer_sdp = await session.handle_offer(offer_sdp)
"""

from __future__ import annotations

import importlib
import json
import logging
import sys
from pathlib import Path

from scheduling.config import settings

log = logging.getLogger("gateway.webrtc")

# ── Import engine-repo's Session via importlib ─────────────────
#
# The scheduling app has its own gateway/ package, which shadows the
# engine-repo's gateway/ package in sys.path.  We use importlib to
# load the engine-repo module directly by file path, avoiding the
# namespace collision.

_ENGINE_REPO = Path(__file__).resolve().parent.parent / "engine-repo"
_ENGINE_WEBRTC = _ENGINE_REPO / "gateway" / "webrtc.py"


def _load_engine_webrtc():
    """Load engine-repo/gateway/webrtc.py as a distinct module.

    The tricky part: the engine's gateway/webrtc.py does
    ``from gateway.audio.audio_queue import AudioQueue``.
    Our project's own ``gateway/`` package shadows that import.

    Fix: temporarily swap sys.modules["gateway"] so the engine's
    imports resolve against engine-repo/gateway/ instead of ours.
    """
    module_name = "_engine_gateway_webrtc"
    if module_name in sys.modules:
        return sys.modules[module_name]

    engine_repo_str = str(_ENGINE_REPO)
    if engine_repo_str not in sys.path:
        sys.path.insert(0, engine_repo_str)

    # Save our gateway package and temporarily remove it
    our_gateway = sys.modules.get("gateway")
    our_gateway_audio = sys.modules.get("gateway.audio")

    # Remove our gateway so the engine's imports resolve correctly
    for key in list(sys.modules.keys()):
        if key == "gateway" or key.startswith("gateway."):
            sys.modules.pop(key, None)

    try:
        # Import the engine's gateway package first so its submodules resolve
        engine_gateway_init = _ENGINE_REPO / "gateway" / "__init__.py"
        gw_spec = importlib.util.spec_from_file_location("gateway", engine_gateway_init,
            submodule_search_locations=[str(_ENGINE_REPO / "gateway")])
        gw_mod = importlib.util.module_from_spec(gw_spec)
        sys.modules["gateway"] = gw_mod
        gw_spec.loader.exec_module(gw_mod)

        # Now load the webrtc module — its `from gateway.audio...` will work
        spec = importlib.util.spec_from_file_location(module_name, _ENGINE_WEBRTC)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        spec.loader.exec_module(mod)
    finally:
        # Restore our gateway package
        if our_gateway is not None:
            sys.modules["gateway"] = our_gateway
        if our_gateway_audio is not None:
            sys.modules["gateway.audio"] = our_gateway_audio

    return mod


# Lazy-load to avoid import-time failures when dependencies
# (aiortc, numpy) are not installed (e.g., in test environments).
_engine_mod = None


def _ensure_loaded():
    global _engine_mod
    if _engine_mod is None:
        _engine_mod = _load_engine_webrtc()
    return _engine_mod


class Session:
    """Proxy for engine-repo's Session class.

    Delegates all attribute access to the real Session instance so
    callers get the full interface (handle_offer, start_recording,
    speak_text, close, etc.) without knowing about the import dance.
    """

    def __init__(self, ice_servers: list = None):
        mod = _ensure_loaded()
        self._real = mod.Session(ice_servers=ice_servers)

    def __getattr__(self, name):
        return getattr(self._real, name)


def ice_servers_to_rtc(servers: list) -> list:
    """Convert ICE server dicts to RTCIceServer objects."""
    mod = _ensure_loaded()
    return mod.ice_servers_to_rtc(servers)


def get_fallback_ice_servers() -> list:
    """Parse the fallback ICE servers from settings.

    Used when Twilio TURN credentials are not available.
    """
    try:
        return json.loads(settings.ice_servers_json)
    except (json.JSONDecodeError, TypeError):
        log.warning("Invalid ICE_SERVERS_JSON in settings, using empty list")
        return []


__all__ = ["Session", "ice_servers_to_rtc", "get_fallback_ice_servers"]
