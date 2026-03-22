"""Generate debate personas from chat log analysis."""

from __future__ import annotations

import json
import logging
import re

from colosseum.core.models import (
    ChatPersonaResult,
    GeneratedPersona,
    ProviderConfig,
)
from colosseum.providers.factory import build_provider
from colosseum.services.chat_parser import extract_speaker_profiles, parse_chat_log

_log = logging.getLogger(__name__)

MAX_SAMPLE_MESSAGES = 30
MIN_MESSAGES_PER_SPEAKER = 3
MAX_SPEAKERS = 8

CHAT_ANALYSIS_SYSTEM_PROMPT = """\
You are a conversation analyst for Colosseum, an AI debate platform.

You will receive messages from one participant in a group conversation.
Analyze their communication patterns and generate a debate persona that captures their UNIQUE voice.

Focus on:
1. Tone and emotional register (formal/casual, warm/cold, aggressive/gentle)
2. Vocabulary habits (favorite words, jargon, filler words, slang)
3. Sentence structure (short/long, fragments, complex clauses, question-heavy)
4. Argumentative style (confrontational, evidence-citing, anecdotal, appeals to authority)
5. Emotional patterns (when they get passionate, dismissive, conciliatory)
6. Catchphrases and verbal tics (repeated openers, pet expressions, unique phrases)

Output EXACTLY this JSON format (no other text):

```json
{
  "persona_name": "Speaker's Name",
  "persona_content": "# Name\\n\\n> One-line description\\n\\n## Your Role\\nDescribe their debate identity...\\n\\n## Debating Style\\n- Bullet points...\\n\\n## Voice Signals\\n- Overall tone: ...\\n- Sentence rhythm: ...\\n- Word choice: ...\\n- Emotional temperature: ...\\n\\n## Speech Patterns\\n- Sentence starters they actually use: ...\\n- Transition phrases: ...\\n- Emphasis style: ...\\n- Punctuation habits: ...\\n\\n## Vocabulary\\n- USE these words (from their actual messages): ...\\n- NEVER USE these words (they never say these): ...\\n\\n## Sample Sentences\\n- Agreeing: \\"...\\"\\n- Disagreeing: \\"...\\"\\n- Citing evidence: \\"...\\"\\n- Making a concession: \\"...\\"\\n\\n## Core Principles\\n- Bullet points...\\n\\n## Watchouts\\n- Bullet points..."
}
```

CRITICAL RULES:
- The Speech Patterns, Vocabulary, and Sample Sentences MUST be derived from the actual messages.
- Sample Sentences should mimic their real sentence structure, not be generic.
- USE vocabulary should contain words they actually used frequently.
- NEVER USE vocabulary should contain formal/informal words they clearly avoid.
- Respond in the same language as the messages.
"""


def _parse_provider_spec(spec: str) -> ProviderConfig:
    """Parse a 'provider:model' spec into ProviderConfig."""
    type_map = {
        "claude": "claude_cli",
        "codex": "codex_cli",
        "gemini": "gemini_cli",
        "ollama": "ollama",
        "hf": "huggingface_local",
        "mock": "mock",
    }
    if ":" not in spec:
        return ProviderConfig(type="claude_cli", model=spec)
    provider, model = spec.split(":", 1)
    ptype = type_map.get(provider, "command")
    kwargs: dict = {"type": ptype, "model": model}
    if ptype in ("ollama", "huggingface_local"):
        kwargs["ollama_model"] = model
    return ProviderConfig(**kwargs)


class ChatPersonaGeneratorService:
    """Analyze a chat log and generate debate personas for each speaker."""

    async def generate(
        self,
        model_spec: str,
        chat_text: str,
    ) -> ChatPersonaResult:
        """Parse chat, analyze each speaker, return one persona per speaker."""
        messages = parse_chat_log(chat_text)
        if not messages:
            raise ValueError("Could not parse any messages from the chat log.")

        profiles = extract_speaker_profiles(messages, min_messages=MIN_MESSAGES_PER_SPEAKER)
        all_speakers = set(m.speaker for m in messages)
        skipped = [s for s in all_speakers if s not in profiles]

        if not profiles:
            raise ValueError(
                f"No speakers with {MIN_MESSAGES_PER_SPEAKER}+ messages found. "
                f"Speakers found: {', '.join(all_speakers)}"
            )

        if len(profiles) > MAX_SPEAKERS:
            _log.warning(
                "Chat has %d speakers, capping at %d", len(profiles), MAX_SPEAKERS
            )
            # Keep the most active speakers
            sorted_profiles = sorted(
                profiles.items(), key=lambda x: x[1].message_count, reverse=True
            )
            dropped = [name for name, _ in sorted_profiles[MAX_SPEAKERS:]]
            skipped.extend(dropped)
            profiles = dict(sorted_profiles[:MAX_SPEAKERS])

        config = _parse_provider_spec(model_spec)
        provider = build_provider(config)

        personas: list[GeneratedPersona] = []
        for name, profile in profiles.items():
            _log.info("Analyzing speaker: %s (%d messages)", name, profile.message_count)
            persona = await self._analyze_speaker(provider, name, profile.messages)
            if persona:
                personas.append(persona)

        return ChatPersonaResult(
            speakers_found=len(all_speakers),
            personas=personas,
            skipped_speakers=skipped,
        )

    async def _analyze_speaker(
        self,
        provider,
        speaker_name: str,
        messages: list[str],
    ) -> GeneratedPersona | None:
        """Call AI to analyze one speaker's messages and generate a persona."""
        # Sample messages for context window management
        sample = messages[:MAX_SAMPLE_MESSAGES]

        prompt_parts = [
            CHAT_ANALYSIS_SYSTEM_PROMPT,
            f"\n--- Messages from {speaker_name} ({len(messages)} total, showing {len(sample)}) ---\n",
        ]
        for i, msg in enumerate(sample, 1):
            prompt_parts.append(f"{i}. {msg}")

        prompt_parts.append(
            f"\n--- End of messages ---\n\n"
            f"Analyze {speaker_name}'s communication patterns and generate their debate persona. "
            f"Return ONLY the JSON."
        )

        full_prompt = "\n".join(prompt_parts)

        try:
            result = await provider.generate(
                operation="chat_persona_analysis",
                instructions=full_prompt,
                metadata={"speaker": speaker_name},
            )
            return self._parse_persona_response(result.content, speaker_name)
        except Exception as exc:
            _log.warning("Failed to analyze speaker %s: %s", speaker_name, exc)
            return None

    @staticmethod
    def _parse_persona_response(raw: str, speaker_name: str) -> GeneratedPersona | None:
        """Extract GeneratedPersona from AI response."""
        for candidate in _extract_json_candidates(raw):
            try:
                data = json.loads(candidate)
                if not isinstance(data, dict):
                    continue
                name = data.get("persona_name", speaker_name)
                content = data.get("persona_content", "")
                if not content:
                    continue
                pid = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "speaker"
                description = _extract_description(content)
                return GeneratedPersona(
                    persona_id=pid,
                    name=name,
                    description=description,
                    content=content,
                )
            except (json.JSONDecodeError, TypeError, KeyError):
                continue
        return None


def _extract_json_candidates(text: str) -> list[str]:
    """Find JSON blocks in text."""
    candidates = []
    for match in re.finditer(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL):
        candidates.append(match.group(1).strip())
    for match in re.finditer(r"\{[^{}]*\"persona_name\"[^}]*\}", text, re.DOTALL):
        candidates.append(match.group(0))
    # Try the whole text
    stripped = text.strip()
    if stripped.startswith("{"):
        candidates.append(stripped)
    return candidates


def _extract_description(content: str) -> str:
    """Extract blockquote description from persona markdown."""
    match = re.search(r"^>\s*(.+)$", content, re.MULTILINE)
    return match.group(1).strip() if match else ""
