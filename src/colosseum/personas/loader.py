"""Compatibility wrapper for legacy callers that still import PersonaLoader."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from colosseum.personas.registry import BUILTIN_DIR, CUSTOM_DIR, PersonaRegistry


class PersonaLoader:
    """Preserve the historical loader API on top of the typed registry."""

    def __init__(
        self,
        builtin_dir: Path | None = None,
        custom_dir: Path | None = None,
    ) -> None:
        self.registry = PersonaRegistry(
            builtin_dir=builtin_dir or BUILTIN_DIR,
            custom_dir=custom_dir or CUSTOM_DIR,
        )

    def list_personas(self) -> list[dict[str, Any]]:
        return [
            {
                "persona_id": persona.persona_id,
                "name": persona.name,
                "description": persona.description,
                "source": persona.source,
                "version": persona.version,
                "tags": persona.tags,
                "active": persona.is_active,
            }
            for persona in self.registry.list_personas()
        ]

    def load_persona(self, persona_id: str) -> str | None:
        return self.registry.get_persona_content(persona_id)

    def save_custom_persona(self, persona_id: str, content: str) -> dict[str, Any]:
        persona = self.registry.save_custom_persona(persona_id, content)
        return {
            "persona_id": persona.persona_id,
            "name": persona.name,
            "description": persona.description,
            "source": persona.source,
            "version": persona.version,
            "tags": persona.tags,
            "active": persona.is_active,
        }

    def delete_custom_persona(self, persona_id: str) -> bool:
        return self.registry.delete_custom_persona(persona_id)
