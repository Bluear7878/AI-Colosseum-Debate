"""Chat log parser for extracting speaker messages from text conversations."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ChatMessage:
    speaker: str
    content: str
    timestamp: str | None = None


@dataclass
class SpeakerProfile:
    name: str
    messages: list[str] = field(default_factory=list)
    message_count: int = 0
    avg_message_length: float = 0.0


# Patterns ordered by specificity (most specific first)
_PATTERNS = [
    # [2024-01-15 10:30:00] Name: message
    re.compile(r"^\[(.+?)\]\s*(.+?):\s*(.+)$"),
    # 1/15/24, 10:30 AM - Name: message (WhatsApp)
    re.compile(
        r"^(\d{1,2}/\d{1,2}/\d{2,4},?\s*\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)?)"
        r"\s*[-–]\s*(.+?):\s*(.+)$",
        re.IGNORECASE,
    ),
    # Name (timestamp): message
    re.compile(r"^(.+?)\s*\((.+?)\):\s*(.+)$"),
    # Name: message (simplest, must be last)
    re.compile(r"^([^:]{1,40}):\s*(.+)$"),
]

# Lines that look like system messages, not actual chat
_SYSTEM_LINE_PATTERNS = [
    re.compile(r"^\s*[-–—]\s*(joined|left|added|removed|changed|created)", re.IGNORECASE),
    re.compile(r"^\s*<.+>$"),  # XML-like tags
    re.compile(r"^\s*\[system\]", re.IGNORECASE),
]


def _is_system_line(line: str) -> bool:
    return any(p.search(line) for p in _SYSTEM_LINE_PATTERNS)


def _try_match(line: str) -> ChatMessage | None:
    """Try all patterns against a line. Return ChatMessage or None."""
    stripped = line.strip()
    if not stripped or _is_system_line(stripped):
        return None

    for i, pattern in enumerate(_PATTERNS):
        m = pattern.match(stripped)
        if not m:
            continue
        groups = m.groups()
        if i == 3:
            # Simple Name: message (no timestamp)
            return ChatMessage(speaker=groups[0].strip(), content=groups[1].strip())
        if i == 2:
            # Name (timestamp): message
            return ChatMessage(
                speaker=groups[0].strip(),
                content=groups[2].strip(),
                timestamp=groups[1].strip(),
            )
        # Patterns 0 and 1: timestamp first, then name, then message
        return ChatMessage(
            speaker=groups[1].strip(),
            content=groups[2].strip(),
            timestamp=groups[0].strip(),
        )
    return None


def parse_chat_log(text: str) -> list[ChatMessage]:
    """Parse a chat log text into structured messages.

    Supports formats:
    - "Name: message"
    - "[timestamp] Name: message"
    - "timestamp - Name: message" (WhatsApp)
    - "Name (timestamp): message"

    Multi-line messages are appended to the previous message.
    """
    messages: list[ChatMessage] = []
    lines = text.splitlines()

    for line in lines:
        if not line.strip():
            continue

        msg = _try_match(line)
        if msg:
            messages.append(msg)
        elif messages:
            # Continuation of previous message
            messages[-1].content += "\n" + line.strip()

    return messages


def extract_speaker_profiles(
    messages: list[ChatMessage],
    min_messages: int = 3,
) -> dict[str, SpeakerProfile]:
    """Group messages by speaker and compute statistics.

    Speakers with fewer than `min_messages` are excluded.
    """
    by_speaker: dict[str, list[str]] = {}
    for msg in messages:
        name = msg.speaker
        if name not in by_speaker:
            by_speaker[name] = []
        by_speaker[name].append(msg.content)

    profiles: dict[str, SpeakerProfile] = {}
    for name, msgs in by_speaker.items():
        if len(msgs) < min_messages:
            continue
        total_len = sum(len(m) for m in msgs)
        profiles[name] = SpeakerProfile(
            name=name,
            messages=msgs,
            message_count=len(msgs),
            avg_message_length=total_len / len(msgs) if msgs else 0.0,
        )

    return profiles
