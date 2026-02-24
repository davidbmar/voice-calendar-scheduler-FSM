# Session

Session-ID: S-2026-02-24-0642-barge-in-preserve-speech
Title: Preserve user speech frames during barge-in
Date: 2026-02-24
Author: Claude

## Goal

Fix barge-in so the user's speech that triggered the interruption is preserved and transcribed, instead of being discarded along with echo frames.

## Context

`_wait_for_playback()` in `gateway/server.py` clears `_mic_frames` entirely on both barge-in (line 194) and normal TTS completion (line 200). This discards the user's speech that triggered barge-in, forcing them to repeat themselves. The VAD loop also resets `has_speech = False` unconditionally after playback (line 331), losing the speech-in-progress state.

## Plan

1. In `_wait_for_playback()`: trim `_mic_frames` to recent speech frames on barge-in instead of clearing; detect late speech on normal TTS completion
2. In the VAD loop: set `has_speech = True` when `_wait_for_playback()` returns `True` (interrupted)

## Changes Made

- `gateway/server.py:_wait_for_playback()`: On barge-in, keep recent frames (~600ms) instead of clearing all. On normal completion, check tail frames for late speech and preserve if detected.
- `gateway/server.py` VAD loop: Set `has_speech = True` when interrupted, so VAD continues collecting speech without requiring re-confirmation.

## Decisions Made

- Keep ~(barge_confirm + 1) * 5 frames on barge-in: this preserves the speech that triggered detection plus a small buffer before it
- On normal TTS completion, check last 3 polls (~300ms) of frames for late speech: catches users who start speaking just as TTS finishes
- Used 5 frames per 100ms poll interval (based on ~20ms per mic frame at 48kHz)

## Open Questions

- None

## Links

Commits:
- (pending)
