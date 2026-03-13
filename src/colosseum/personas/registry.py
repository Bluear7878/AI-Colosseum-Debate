"""Registry and parsing helpers for builtin and custom personas."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from colosseum.core.models import PersonaDefinition

BUILTIN_DIR = Path(__file__).resolve().parent / "builtin"
CUSTOM_DIR = Path(__file__).resolve().parent / "custom"

_PERSONA_ID_SANITIZER = re.compile(r"[^a-z0-9_]")


def sanitize_persona_id(persona_id: str) -> str:
    """Normalize user-provided persona ids into filesystem-safe identifiers."""
    normalized = _PERSONA_ID_SANITIZER.sub("_", persona_id.lower().strip()).strip("_")
    return normalized or "custom_persona"


class PersonaRegistry:
    """Load, validate, and persist personas across builtin and custom sources."""

    def __init__(
        self,
        builtin_dir: Path | None = None,
        custom_dir: Path | None = None,
    ) -> None:
        self.builtin_dir = builtin_dir or BUILTIN_DIR
        self.custom_dir = custom_dir or CUSTOM_DIR

    def list_personas(self) -> list[PersonaDefinition]:
        """Return the effective persona catalogue with source precedence applied."""
        by_id: dict[str, PersonaDefinition] = {}
        for source, path in self._iter_persona_paths():
            definition = self._parse_persona_file(path, source=source)
            by_id.setdefault(definition.persona_id, definition)
        return sorted(by_id.values(), key=lambda item: (item.source, item.persona_id))

    def get_persona(self, persona_id: str) -> PersonaDefinition | None:
        """Return the resolved persona definition or ``None`` if missing."""
        target = sanitize_persona_id(persona_id)
        for source, path in self._iter_persona_paths():
            definition = self._parse_persona_file(path, source=source)
            if definition.persona_id == target:
                return definition
        return None

    def get_persona_content(self, persona_id: str) -> str | None:
        """Return prompt content without frontmatter metadata."""
        definition = self.get_persona(persona_id)
        return definition.content if definition else None

    def save_custom_persona(self, persona_id: str, content: str) -> PersonaDefinition:
        """Persist a custom persona and return its parsed metadata."""
        safe_id = sanitize_persona_id(persona_id)
        normalized_content = content.strip()
        if not normalized_content:
            raise ValueError("Persona content must be non-empty.")

        self.custom_dir.mkdir(parents=True, exist_ok=True)
        path = self.custom_dir / f"{safe_id}.md"
        path.write_text(normalized_content + "\n", encoding="utf-8")
        return self._parse_persona_file(path, source="custom")

    def delete_custom_persona(self, persona_id: str) -> bool:
        """Delete a custom persona by id."""
        path = self.custom_dir / f"{sanitize_persona_id(persona_id)}.md"
        if not path.exists():
            return False
        path.unlink()
        return True

    def _iter_persona_paths(self) -> list[tuple[str, Path]]:
        ordered_paths: list[tuple[str, Path]] = []
        if self.custom_dir.exists():
            ordered_paths.extend(("custom", path) for path in sorted(self.custom_dir.glob("*.md")))
        if self.builtin_dir.exists():
            ordered_paths.extend(
                ("builtin", path) for path in sorted(self.builtin_dir.glob("*.md"))
            )
        return ordered_paths

    def _parse_persona_file(self, path: Path, source: str) -> PersonaDefinition:
        text = path.read_text(encoding="utf-8")
        metadata, body = self._split_frontmatter(text)
        lines = [line.rstrip() for line in body.strip().splitlines() if line.strip()]

        persona_id = sanitize_persona_id(
            str(metadata.get("id") or metadata.get("persona_id") or path.stem)
        )
        name = str(metadata.get("name") or path.stem.replace("_", " ").title()).strip()
        description = str(metadata.get("description") or "").strip()
        version = str(metadata.get("version") or "1.0").strip() or "1.0"
        tags = self._parse_tags(metadata.get("tags"))
        is_active = self._parse_bool(metadata.get("active"), default=True)

        for line in lines:
            if line.startswith("# "):
                name = line[2:].strip() or name
            elif line.startswith("> ") and not description:
                description = line[2:].strip()

        return PersonaDefinition(
            persona_id=persona_id,
            name=name,
            description=description,
            source=source,
            version=version,
            tags=tags,
            is_active=is_active,
            content=body.strip(),
            content_path=str(path),
        )

    def _split_frontmatter(self, text: str) -> tuple[dict[str, Any], str]:
        if not text.startswith("---\n"):
            return {}, text

        marker = "\n---\n"
        end_index = text.find(marker, 4)
        if end_index < 0:
            return {}, text

        raw_header = text[4:end_index]
        body = text[end_index + len(marker) :]
        metadata: dict[str, Any] = {}
        for line in raw_header.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or ":" not in stripped:
                continue
            key, raw_value = stripped.split(":", 1)
            metadata[key.strip().lower()] = self._parse_scalar(raw_value.strip())
        return metadata, body

    def _parse_scalar(self, value: str) -> Any:
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            if not inner:
                return []
            return [item.strip().strip("'\"") for item in inner.split(",") if item.strip()]
        lowered = value.lower()
        if lowered in {"true", "false"}:
            return lowered == "true"
        return value.strip().strip("'\"")

    def _parse_tags(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [item.strip() for item in value.split(",") if item.strip()]
        return []

    def _parse_bool(self, value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "yes", "1"}:
                return True
            if lowered in {"false", "no", "0"}:
                return False
        return default
