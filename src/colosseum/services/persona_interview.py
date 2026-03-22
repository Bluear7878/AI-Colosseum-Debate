"""Interactive persona interview service.

Conducts a multi-turn conversation with the user via a selected AI model
to build a debate persona through guided questions.
"""

from __future__ import annotations

import json
import logging
import re

from colosseum.core.models import (
    GeneratedPersona,
    PersonaInterviewMessage,
    PersonaInterviewResult,
    ProviderConfig,
)
from colosseum.providers.factory import build_provider

_log = logging.getLogger(__name__)

INTERVIEW_SYSTEM_PROMPT = """\
You are a debate persona architect for Colosseum, an AI debate platform.
Your job is to interview the user and craft a unique debate persona with a DISTINCTIVE voice.

RULES:
1. Ask ONE question at a time. Keep questions short and conversational.
2. Start by asking about their professional background or role.
3. Then explore these topics (one question per turn):
   - How they argue and what they value in debates
   - Their pet peeves and things that annoy them in discussions
   - Their strengths and weaknesses when arguing
   - Ask for an example: "How would you push back on an idea you disagree with? Give me a sentence or two."
   - Ask about verbal habits: "Are there phrases you catch yourself saying a lot?"
4. After 5-7 exchanges (when you have enough), generate the final persona.
5. When you have enough information, respond with EXACTLY this JSON format (no other text):

```json
{"done": true, "persona_name": "...", "persona_content": "# Name\\n\\n> description\\n\\n## Your Role\\n...\\n\\n## Debating Style\\n- ...\\n\\n## Voice Signals\\n- Overall tone: ...\\n- Sentence rhythm: ...\\n- Word choice: ...\\n- Emotional temperature: ...\\n\\n## Speech Patterns\\n- Sentence starters: ...\\n- Transitions: ...\\n- Emphasis style: ...\\n- Punctuation: ...\\n\\n## Vocabulary\\n- USE: ...\\n- NEVER USE: ...\\n\\n## Sample Sentences\\n- Agreeing: \\"....\\"\\n- Disagreeing: \\"....\\"\\n- Citing evidence: \\"....\\"\\n- Conceding: \\"....\\"\\n\\n## Core Principles\\n- ...\\n\\n## Watchouts\\n- ..."}
```

CRITICAL - The persona MUST include ALL these sections:
- Your Role, Debating Style, Voice Signals
- Speech Patterns (concrete sentence starters, transitions, emphasis, punctuation habits)
- Vocabulary (words they USE frequently + words they NEVER USE)
- Sample Sentences (how they agree, disagree, cite evidence, concede — based on their actual answers)
- Core Principles, Watchouts

The Speech Patterns and Sample Sentences should reflect the user's ACTUAL phrasing from the interview.
Each persona must sound DISTINCTLY different from others — not generic assistant prose.

6. While interviewing, respond with plain text only (your question). No JSON until you're done.
7. Be warm, curious, and encouraging. Make the user feel like they're having a fun conversation.
8. Respond in the same language the user uses.
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


class PersonaInterviewService:
    """Conducts a multi-turn persona interview using a selected AI model."""

    async def step(
        self,
        model_spec: str,
        messages: list[PersonaInterviewMessage],
    ) -> PersonaInterviewResult:
        """Process one interview step: send history to AI, return response."""
        config = _parse_provider_spec(model_spec)
        provider = build_provider(config)

        # Build the prompt with full conversation history
        prompt_parts = [INTERVIEW_SYSTEM_PROMPT, ""]

        for msg in messages:
            role_label = "User" if msg.role == "user" else "Interviewer"
            prompt_parts.append(f"{role_label}: {msg.content}")

        prompt_parts.append("")
        prompt_parts.append(
            "Interviewer (respond with your next question, "
            "or the final JSON if you have enough info):"
        )

        full_prompt = "\n".join(prompt_parts)

        result = await provider.generate(
            operation="persona_interview",
            instructions=full_prompt,
            metadata={"model": config.model},
        )

        raw = result.content.strip()

        # Check if the AI returned the final persona JSON
        persona = self._try_parse_done(raw)
        if persona:
            return PersonaInterviewResult(
                message="Your persona has been created!",
                done=True,
                persona=persona,
            )

        # Still interviewing — return the question
        # Strip any JSON wrapper if the provider wrapped it
        clean = self._extract_text(raw)
        return PersonaInterviewResult(message=clean, done=False)

    @staticmethod
    def _try_parse_done(raw: str) -> GeneratedPersona | None:
        """Try to extract a done=true JSON from the AI response."""
        # Try direct JSON parse
        for candidate in _extract_json_candidates(raw):
            try:
                data = json.loads(candidate)
                if isinstance(data, dict) and data.get("done"):
                    name = data.get("persona_name", "Custom Persona")
                    content = data.get("persona_content", "")
                    if content:
                        pid = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
                        return GeneratedPersona(
                            persona_id=pid or "custom",
                            name=name,
                            description=_extract_description(content),
                            content=content,
                        )
            except (json.JSONDecodeError, TypeError, KeyError):
                continue
        return None

    @staticmethod
    def _extract_text(raw: str) -> str:
        """Extract clean text from provider response, stripping JSON wrappers."""
        stripped = raw.strip()
        # If it looks like a JSON wrapper with 'content' field
        if stripped.startswith("{"):
            try:
                data = json.loads(stripped)
                if isinstance(data, dict) and "content" in data:
                    return str(data["content"]).strip()
            except json.JSONDecodeError:
                pass
        return stripped


def _extract_json_candidates(text: str) -> list[str]:
    """Find JSON blocks in text (both fenced and bare)."""
    candidates = []
    # Fenced code blocks
    for match in re.finditer(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL):
        candidates.append(match.group(1).strip())
    # Bare JSON objects
    for match in re.finditer(r"\{[^{}]*\"done\"[^{}]*\}", text, re.DOTALL):
        candidates.append(match.group(0))
    # Full text as fallback
    candidates.append(text.strip())
    return candidates


def _extract_description(content: str) -> str:
    """Extract the blockquote description from persona markdown."""
    match = re.search(r"^>\s*(.+)$", content, re.MULTILINE)
    return match.group(1).strip() if match else ""
