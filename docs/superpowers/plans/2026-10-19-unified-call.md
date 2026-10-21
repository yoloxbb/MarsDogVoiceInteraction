# Unified Call Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline. Track verification below.

**Goal:** Publish one session-only call event with a VAD bearing bound to its audio.

**Architecture:** Reader owns a separate bounded VAD bearing history. Audio capture
retains chunk timestamps; the node freezes direction before asynchronous processing.
A small call module normalizes events, snapshots directions, and deduplicates calls.

**Tech Stack:** Python 3.10, ROS2 Humble, pytest, existing ReSpeaker serial transport.

**Spec:** `docs/superpowers/specs/2026-10-19-unified-call-design.md`

## Global Constraints

- Canonical `EVT_VOICE_CALL`, `call_kind=here|name`, required source, schema v2.
- Session-only; no wake, capture cancellation, new execution authority, or remote publication.
- Publish calls even with invalid direction; 600 ms bearing age at publication.
- Keep changes in the user's current checkout, uncommitted, for IDE review.

## Review Focus

- ASR backlog must never attach the next speaker's direction to old audio.
- Invalid updates, missing timestamps and reconnects must fail direction closed.
- A model CALL plus an explicit action must retain the separate action event.
- Deduplication must not suppress different actual utterances.
- Generic sound-source gating and hardware wake must keep their existing behavior.

### Task 1: VAD history and audio timestamps

Files: `adapters/wakeup/respeaker_serial_reader.py`, `providers/audio_capture.py`,
`providers/audio_sherpa.py`, `tests/test_unified_call.py`, `tests/test_audio_capture.py`.
Interfaces: `recent_call_doa(since,until)` returns a measured-time bearing;
`read_chunk()` returns timestamp plus samples; audio results carry speech intervals.
- [x] Write and run failing tests for VAD-only snapshots, invalid/out-of-order samples,
  disconnects, and buffered speech capture times.
- [x] Implement the interfaces, keeping `read()` compatible.
- [x] Run the reader/audio regression tests.

### Task 2: Canonical call routing and frozen directions

Files: new `core/call_event.py`, `messages/audio_event.py`, `messages/voice_event_types.py`,
`nodes/voice_interaction_node.py`, both ReSpeaker providers, `core/command_lexicon.py`,
`messages/intent_event_router.py`, call/session/contract tests.
Interfaces: direction snapshot dictionaries carry immutable measurement times and
reader generation; `bind_utterance_direction(audio_data,wakeup)` freezes once;
`CallDeduplicator` compares utterance IDs and cross-source acoustic times.
- [x] Write and run failing tests for canonical calls, directionless publication,
  delayed ASR speaker isolation, legacy ingress, deduplication and session gates.
- [x] Implement and update superseded assertions, retaining wake/COME behavior.
- [x] Run focused tests and inspect any failures against the spec.

### Task 3: Generated contracts, documentation and verification

Files: catalog rules and generated catalog/KWS/docs, README, ROS2/ReSpeaker/downstream docs.
- [x] Regenerate artifacts; check canonical call IDs, subtype/source, and permissions.
- [x] Document migration, snapshot timing, strict validity and downstream responsibilities.
- [x] Run generator checks, focused tests and full pytest; report existing failures separately.
- [x] Run a fresh independent review and address material findings with regression tests.

## Execution notes

Ruling: The user explicitly requested implementation of the accepted in-chat design;
continue inline without another approval cycle. No commits, worktree changes,
node restarts, or changes to other repositories are part of this request.
Baseline: HEAD 4377ffb; prior full run exposed catalog count 221 versus 222 assertions.

Verification before final review: focused 521 passed; full 1049 passed, 20 skipped.
Independent review found two important issues, no minor findings: call age inherited
a larger wake setting; one firmware anchor could consume adjacent ASR utterances.
Both were reproduced with failing regressions; fixes cap calls at min(600,wake limit)
and bind each firmware match to one utterance. Firmware audio age now anchors
cross-source matching and the pre-session gate. Final full verification: 1053 passed, 20 skipped (100.75 s). Generator checks, compileall and git diff --check passed. Both independent review findings are fixed; no deferred minor findings. No build, restart, hardware acceptance, commit or push performed.

2026-10-20 follow-up: firmware reports VAD only on boundaries/direction changes.
Regression reproduced a valid keyword-only bearing being lost, and valid start
being ignored. Filter state/validity before recording; collect start/update and
keyword measurements with full snapshot age; preserve raw notifications and the
600 ms publication cap. Call trace summaries expose doa fields.
Follow-up verification: 351 focused tests passed; full suite 1065 passed,
20 skipped (104.35 s). Updated the old age-regression fixture to use an actual
vad/update message with type/state. Generator checks, compileall and diff check
passed. No node restart or hardware acceptance performed.
