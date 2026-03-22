"""Tests for chat log parser."""

from __future__ import annotations

from colosseum.services.chat_parser import (
    ChatMessage,
    extract_speaker_profiles,
    parse_chat_log,
)


def test_parse_simple_name_colon():
    text = "Alice: Hello\nBob: Hi there\nAlice: How are you?"
    msgs = parse_chat_log(text)
    assert len(msgs) == 3
    assert msgs[0].speaker == "Alice"
    assert msgs[0].content == "Hello"
    assert msgs[1].speaker == "Bob"


def test_parse_timestamp_bracket():
    text = "[2024-01-15 10:30] Alice: Hello\n[2024-01-15 10:31] Bob: Hi"
    msgs = parse_chat_log(text)
    assert len(msgs) == 2
    assert msgs[0].speaker == "Alice"
    assert msgs[0].timestamp == "2024-01-15 10:30"


def test_parse_whatsapp_format():
    text = "1/15/24, 10:30 AM - Alice: Hello\n1/15/24, 10:31 AM - Bob: Hi"
    msgs = parse_chat_log(text)
    assert len(msgs) == 2
    assert msgs[0].speaker == "Alice"
    assert msgs[0].content == "Hello"


def test_parse_multiline():
    text = "Alice: First line\nsecond line\nBob: Reply"
    msgs = parse_chat_log(text)
    assert len(msgs) == 2
    assert "second line" in msgs[0].content
    assert msgs[1].content == "Reply"


def test_parse_empty_lines_skipped():
    text = "Alice: Hello\n\n\nBob: Hi"
    msgs = parse_chat_log(text)
    assert len(msgs) == 2


def test_parse_empty_text():
    assert parse_chat_log("") == []
    assert parse_chat_log("   \n\n  ") == []


def test_extract_profiles_groups_by_speaker():
    msgs = [
        ChatMessage(speaker="Alice", content="msg1"),
        ChatMessage(speaker="Bob", content="msg2"),
        ChatMessage(speaker="Alice", content="msg3"),
        ChatMessage(speaker="Alice", content="msg4"),
        ChatMessage(speaker="Bob", content="msg5"),
        ChatMessage(speaker="Bob", content="msg6"),
    ]
    profiles = extract_speaker_profiles(msgs, min_messages=3)
    assert "Alice" in profiles
    assert "Bob" in profiles
    assert profiles["Alice"].message_count == 3
    assert profiles["Bob"].message_count == 3


def test_extract_profiles_skips_low_count():
    msgs = [
        ChatMessage(speaker="Alice", content="msg1"),
        ChatMessage(speaker="Alice", content="msg2"),
        ChatMessage(speaker="Alice", content="msg3"),
        ChatMessage(speaker="Bob", content="msg4"),
    ]
    profiles = extract_speaker_profiles(msgs, min_messages=3)
    assert "Alice" in profiles
    assert "Bob" not in profiles


def test_extract_profiles_avg_length():
    msgs = [
        ChatMessage(speaker="A", content="hi"),
        ChatMessage(speaker="A", content="hello"),
        ChatMessage(speaker="A", content="hey there"),
    ]
    profiles = extract_speaker_profiles(msgs, min_messages=1)
    avg = profiles["A"].avg_message_length
    assert avg > 0
    assert abs(avg - (2 + 5 + 9) / 3) < 0.01
