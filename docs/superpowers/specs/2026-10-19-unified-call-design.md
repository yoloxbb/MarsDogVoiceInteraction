# Unified call events and VAD bearings

Approved in chat on 2026-10-19: unify CALL_HERE and both CALL_NAME routes as
`EVT_VOICE_CALL`, with required `call_kind=here|name` and `intent_source`.
Calls remain session-only social notifications, with both execution flags false.
Hardware wake and explicit COME commands remain separate. No Tree/Action changes.

Updated by the user on 2026-10-20: accept valid ReSpeaker `vad/start|update`
(active must be true when present) and `keyword` bearings. Strict JSON true,
finite numeric angles in [0,360), and finite nonnegative ages are required.
Keyword measurement time subtracts both doa_age_ms and audio_age_ms; VAD
subtracts doa_age_ms. State filtering precedes recording; VAD end is ignored.
Raw queues still retain boundaries and directionless recognitions. Invalid
updates never refresh old measurements. Measurements older than the newest stored
one are ignored. Disconnect/reconnect clears history and invalidates snapshots.
Collection is independent of `sound_source.enabled` and uses the existing reader.

Microphone chunks retain their original host capture timestamps through buffering.
Speech intervals are derived from actual VAD sample boundaries. Before ASR or queue
submission, bind a copy of the latest valid bearing from that interval to the
utterance, including missing bearings. Later speech cannot replace the snapshot.
Firmware call keywords use their bounded recognition-time window (firmware has no
precise word boundaries). The 600 ms maximum is checked against publication time,
not reset when the snapshot is copied. Unknown/expired/disconnected directions are
published with `doa=-1,doa_valid=false`; the call itself is still published.

Normalize legacy call events on ingress, emit only the canonical event, preserve
per-utterance speaker evidence without inheriting wake identity, and deduplicate
same-utterance or matching cross-source acoustic-time notifications. A rejected
idle/stale call does not consume deduplication state. Keep original trigger and
measurement metadata internal; published direction ages align with header.stamp.

Validation includes zero degrees, malformed numeric/boolean ages, source isolation,
out-of-order samples, reconnection, delayed ASR with another speaker, missing audio
timestamps, expired snapshots, non-interruption of capture, and canonical schema
normalization. Regenerate catalog/KWS artifacts from the reviewed rules.
