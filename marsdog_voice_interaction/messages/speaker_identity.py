"""Fixed speaker identities shared by enrollment and event routing."""

from __future__ import annotations

from enum import Enum


class SpeakerIdentity(str, Enum):
    """Device-local identity slots exposed by the speaker API."""

    OWNER = "owner"
    FAMILY_MEMBER_1 = "family_member_1"
    FAMILY_MEMBER_2 = "family_member_2"
    FAMILY_MEMBER_3 = "family_member_3"
    FAMILY_MEMBER_4 = "family_member_4"


ALLOWED_SPEAKER_IDENTITIES = tuple(item.value for item in SpeakerIdentity)


def validate_speaker_identity(value: str | SpeakerIdentity) -> str:
    """Return an allowed identity or raise a stable validation error."""
    raw_value = value.value if isinstance(value, SpeakerIdentity) else str(value)
    if raw_value not in ALLOWED_SPEAKER_IDENTITIES:
        allowed = "、".join(ALLOWED_SPEAKER_IDENTITIES)
        raise ValueError(f"声纹身份只能是：{allowed}")
    return raw_value


def speaker_identity_role(value: str | SpeakerIdentity) -> str:
    """Classify a recognition result without treating legacy names as owner."""
    raw_value = value.value if isinstance(value, SpeakerIdentity) else str(value)
    if raw_value == SpeakerIdentity.OWNER.value:
        return "owner"
    if raw_value in ALLOWED_SPEAKER_IDENTITIES[1:]:
        return "family"
    return "unmaster"
