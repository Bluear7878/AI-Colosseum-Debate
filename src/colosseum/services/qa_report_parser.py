"""Layered, fail-soft parser for QA gladiator reports.

Each gladiator follows the QA skill's `report_template.md`, which is well
structured but not strictly enforced. Gladiators may add or omit sections,
rename headers, or include extra context. The parser must:

  * never raise on malformed input
  * extract what it can, layer by layer
  * mark `parse_status="degraded"` when fields are missing
  * preserve raw text in `raw_unstructured_sections` for the synthesizer

Layers:
  1. Section split by `## ` (or `# `) headers
  2. Bug-block split inside the "Confirmed Bugs" section by `### ` headers
  3. Field extraction (Symptom, Reproduction, Error, Root Cause, File, Severity)
"""

from __future__ import annotations

import re
from typing import Literal

from colosseum.core.models import (
    QAFinding,
    QAFindingSeverity,
    QAFindingStatus,
)


SEVERITY_PATTERNS = {
    QAFindingSeverity.CRITICAL: re.compile(r"\bcritical\b", re.IGNORECASE),
    QAFindingSeverity.HIGH: re.compile(r"\bhigh\b", re.IGNORECASE),
    QAFindingSeverity.MEDIUM: re.compile(r"\bmedium\b|\bmoderate\b|\bmed\b", re.IGNORECASE),
    QAFindingSeverity.LOW: re.compile(r"\blow\b|\bminor\b", re.IGNORECASE),
    QAFindingSeverity.INFO: re.compile(r"\binfo\b|\bnote\b", re.IGNORECASE),
}

CONFIRMED_BUGS_HEADERS = (
    "confirmed bugs (reproduced)",
    "confirmed bugs",
    "reproduced bugs",
    "bugs found",
    "findings",
    "issues found",
    "bugs",
)

FALSE_POSITIVE_HEADERS = (
    "false positives filtered",
    "false positives",
    "filtered candidates",
)

VERIFIED_FIXES_HEADERS = (
    "verified fixes",
)

BUG_HEADER_RE = re.compile(r"^###+\s+(.+)$")  # h3 or deeper for individual bugs
SECTION_HEADER_RE = re.compile(r"^##\s+(.+)$")  # h2 only for top-level sections
TOP_HEADER_RE = re.compile(r"^#\s+(.+)$")  # h1 — title only, used for preamble split
FIELD_RE = re.compile(
    r"^\s*[-*]?\s*\*?\*?\s*(symptom|reproduction|error|root\s*cause|file|severity|status)\s*\*?\*?\s*[:：]\s*(.*)$",
    re.IGNORECASE,
)
FILE_LINE_RE = re.compile(r"`?([^\s`:]+\.[a-zA-Z0-9_]+)(?::(\d+))?`?")
G_NUMBER_RE = re.compile(r"\bG[-_]?(\d{2,4})\b")


def _normalize_file_path(raw: str) -> tuple[str | None, int | None]:
    if not raw:
        return None, None
    match = FILE_LINE_RE.search(raw)
    if not match:
        return raw.strip().strip("`") or None, None
    file_path = match.group(1)
    line_str = match.group(2)
    line_no: int | None = None
    if line_str:
        try:
            line_no = int(line_str)
        except ValueError:
            line_no = None
    return file_path, line_no


def _parse_severity(raw: str) -> QAFindingSeverity:
    for sev, pattern in SEVERITY_PATTERNS.items():
        if pattern.search(raw):
            return sev
    return QAFindingSeverity.MEDIUM


def _strip_traceback_addresses(text: str) -> str:
    """Remove memory addresses, hex pointers, line numbers from traceback noise.

    Used inside the clusterer's signature so two reports of the same bug get
    the same hash even if their traceback addresses differ.
    """
    if not text:
        return ""
    cleaned = re.sub(r"0x[0-9a-fA-F]+", "0xADDR", text)
    cleaned = re.sub(r"\bline \d+\b", "line N", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


class QAReportParser:
    """Layered, fail-soft Markdown report parser."""

    def parse(
        self, report_md: str, gladiator_id: str
    ) -> tuple[list[QAFinding], dict[str, str], Literal["ok", "degraded", "failed", "skipped"]]:
        if not report_md or not report_md.strip():
            return [], {}, "skipped"

        try:
            sections = self._split_sections(report_md)
        except Exception:
            return [], {"_raw": report_md}, "failed"

        parse_status: Literal["ok", "degraded", "failed", "skipped"] = "ok"
        findings: list[QAFinding] = []

        bug_section = self._find_section(sections, CONFIRMED_BUGS_HEADERS)
        if bug_section is None:
            parse_status = "degraded"
        else:
            try:
                blocks = self._split_bug_blocks(bug_section)
                for block in blocks:
                    finding = self._parse_bug_block(block, gladiator_id)
                    if finding is not None:
                        findings.append(finding)
            except Exception:
                parse_status = "degraded"

        # Even when there's no Confirmed Bugs section, scan the whole report for
        # G-NNN style bug headers as a salvage path.
        if not findings:
            try:
                blocks = self._split_bug_blocks(report_md)
                for block in blocks:
                    finding = self._parse_bug_block(block, gladiator_id)
                    if finding is not None:
                        findings.append(finding)
                if findings:
                    parse_status = "degraded" if parse_status != "failed" else parse_status
            except Exception:
                pass

        unstructured: dict[str, str] = {}
        # Preserve all sections except the bug list (already parsed) for the
        # synthesizer to read.
        for header, body in sections.items():
            if any(h in header.lower() for h in CONFIRMED_BUGS_HEADERS):
                continue
            unstructured[header] = body[:6000]

        return findings, unstructured, parse_status

    # ── layer 1: section split ──────────────────────────────────────

    def _split_sections(self, md: str) -> dict[str, str]:
        """Split into sections by H2 headers (`## `).

        H1 (`# `) is treated as a title and the H1 line itself is dropped from
        the preamble. H3+ (`### `, `#### `) headers stay inside their parent
        section so the bug-block parser can find them.
        """
        sections: dict[str, str] = {}
        current_header = "_preamble"
        current_lines: list[str] = []
        for line in md.splitlines():
            section_match = SECTION_HEADER_RE.match(line)
            if section_match:
                if current_lines:
                    sections[current_header] = "\n".join(current_lines).strip()
                current_header = section_match.group(1).strip()
                current_lines = []
                continue
            current_lines.append(line)
        if current_lines:
            sections[current_header] = "\n".join(current_lines).strip()
        return sections

    def _find_section(self, sections: dict[str, str], needles: tuple[str, ...]) -> str | None:
        for header, body in sections.items():
            lowered = header.lower()
            for needle in needles:
                if needle in lowered:
                    return body
        return None

    # ── layer 2: bug block split ────────────────────────────────────

    def _split_bug_blocks(self, text: str) -> list[str]:
        blocks: list[str] = []
        current: list[str] = []
        in_block = False
        for line in text.splitlines():
            if line.startswith("### "):
                if current and in_block:
                    blocks.append("\n".join(current).strip())
                current = [line]
                in_block = True
            else:
                if in_block:
                    current.append(line)
        if current and in_block:
            blocks.append("\n".join(current).strip())
        return [b for b in blocks if b]

    # ── layer 3: field extraction ───────────────────────────────────

    def _parse_bug_block(self, block: str, gladiator_id: str) -> QAFinding | None:
        if not block:
            return None
        lines = block.splitlines()
        if not lines:
            return None
        header_line = lines[0].lstrip("#").strip()
        # Strip "[NEW]" or similar markers
        header_line = re.sub(r"^\[\s*[A-Z]+\s*\]\s*", "", header_line)
        # Extract G-NNN if present
        raw_bug_id = None
        g_match = G_NUMBER_RE.search(header_line)
        if g_match:
            raw_bug_id = f"G-{g_match.group(1)}"
            # Strip the G-NNN: from the title
            header_line = re.sub(r"\bG[-_]?\d+\s*[:.\-]?\s*", "", header_line, count=1).strip()
        title = header_line.lstrip(":").strip() or "(untitled bug)"

        fields: dict[str, str] = {}
        current_field: str | None = None
        current_buf: list[str] = []
        for raw in lines[1:]:
            match = FIELD_RE.match(raw)
            if match:
                if current_field is not None:
                    fields[current_field] = "\n".join(current_buf).strip()
                current_field = re.sub(r"\s+", "_", match.group(1).strip().lower())
                current_buf = [match.group(2).strip()]
            else:
                if current_field is not None:
                    current_buf.append(raw.strip(" -*"))
        if current_field is not None:
            fields[current_field] = "\n".join(current_buf).strip()

        symptom = fields.get("symptom", "")
        reproduction = fields.get("reproduction", "")
        error_evidence = fields.get("error", "")
        root_cause = fields.get("root_cause", "")
        severity_raw = fields.get("severity", "")
        file_raw = fields.get("file", "")
        status_raw = fields.get("status", "").lower()

        file_path, line_hint = _normalize_file_path(file_raw)

        if "false" in status_raw:
            status = QAFindingStatus.FALSE_POSITIVE
        elif "unverified" in status_raw or "unknown" in status_raw:
            status = QAFindingStatus.UNVERIFIED
        else:
            status = QAFindingStatus.REPRODUCED

        # If almost no fields were extracted but we have a header, salvage as
        # an unstructured finding using the entire block as the symptom.
        if not any((symptom, reproduction, error_evidence, root_cause)):
            symptom = "\n".join(lines[1:]).strip()[:600]

        return QAFinding(
            title=title,
            symptom=symptom,
            reproduction=reproduction,
            error_evidence=error_evidence,
            root_cause=root_cause,
            file_path=file_path,
            line_hint=line_hint,
            severity=_parse_severity(severity_raw or symptom or title),
            status=status,
            sources=[gladiator_id],
            raw_bug_id=raw_bug_id,
            first_seen_by=gladiator_id,
        )
