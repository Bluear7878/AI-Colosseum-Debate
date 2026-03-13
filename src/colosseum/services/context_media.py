"""Helpers for extracting and summarizing binary context attachments."""

from __future__ import annotations

from typing import Any

from colosseum.core.models import FrozenContextBundle


def extract_image_inputs(bundle: FrozenContextBundle | None) -> list[dict[str, Any]]:
    """Return normalized image attachments from a frozen context bundle."""
    if bundle is None:
        return []

    image_inputs: list[dict[str, Any]] = []
    for source in bundle.sources:
        for fragment in source.fragments:
            media_type = fragment.media_type or str(source.metadata.get("media_type", ""))
            if not fragment.is_binary or not media_type.startswith("image/"):
                continue
            image_inputs.append(
                {
                    "source_id": source.source_id,
                    "label": fragment.label,
                    "path": fragment.path or source.resolved_path,
                    "media_type": media_type,
                    "checksum": fragment.checksum,
                    "size_bytes": fragment.size_bytes,
                    "inline_data": fragment.inline_data,
                }
            )
    return image_inputs


def summarize_image_inputs(image_inputs: list[dict[str, Any]], limit: int = 4) -> str:
    """Build a compact prompt-safe summary of attached images."""
    if not image_inputs:
        return "No shared image inputs."

    entries: list[str] = []
    for item in image_inputs[:limit]:
        size_bytes = item.get("size_bytes") or 0
        size_text = f"{round(size_bytes / 1024, 1)} KB" if size_bytes else "size unknown"
        entries.append(
            f"{item['label']} ({item['media_type']}, {size_text}, checksum {str(item['checksum'])[:8]})"
        )

    remainder = len(image_inputs) - len(entries)
    if remainder > 0:
        entries.append(f"+{remainder} more image(s)")
    return f"{len(image_inputs)} shared image(s): " + "; ".join(entries)
