"""Cross-gladiator finding clusterer for QA ensemble runs.

The clusterer takes the union of all gladiator findings and groups them into
buckets that probably represent the same underlying bug. The judge synthesizer
then receives clusters (instead of N×M raw findings) and can dedup, split bad
clusters, and write canonical bug text from the merged evidence.

Signature design:
  (normalized_path, line_bucket, severity, sha1(symptom_first_80_normalized))

Two findings collide on signature → they go in the same cluster. This is a
fast O(N) single pass; LLM-assisted dedup happens later in the synthesizer.
"""

from __future__ import annotations

import hashlib
import os

from colosseum.core.config import QA_LINE_BUCKET_SIZE
from colosseum.core.models import QAFinding, QAGladiatorOutcome
from colosseum.services.qa_report_parser import _strip_traceback_addresses


def _normalize_path(raw: str | None, target_root: str | None = None) -> str:
    if not raw:
        return ""
    path = raw.strip().strip("`")
    if target_root:
        try:
            target = os.path.abspath(target_root)
            absolute = os.path.abspath(path) if os.path.isabs(path) else os.path.abspath(
                os.path.join(target, path)
            )
            if absolute.startswith(target + os.sep) or absolute == target:
                path = os.path.relpath(absolute, target)
        except Exception:
            pass
    return path.replace("\\", "/").lstrip("./")


def _signature(finding: QAFinding, target_root: str | None = None) -> tuple:
    path = _normalize_path(finding.file_path, target_root)
    line_bucket: int | None = None
    if finding.line_hint is not None:
        line_bucket = finding.line_hint // QA_LINE_BUCKET_SIZE
    symptom_norm = _strip_traceback_addresses(finding.symptom)[:80].lower()
    digest = hashlib.sha1(symptom_norm.encode("utf-8")).hexdigest()[:16]
    return (path, line_bucket, finding.severity.value, digest)


class QAFindingClusterer:
    """Bucket gladiator findings into probable-bug clusters by signature."""

    def __init__(self, target_root: str | None = None) -> None:
        self.target_root = target_root

    def cluster(self, outcomes: list[QAGladiatorOutcome]) -> list[list[QAFinding]]:
        all_findings: list[QAFinding] = []
        for outcome in outcomes:
            all_findings.extend(outcome.parsed_findings)
        if not all_findings:
            return []

        buckets: dict[tuple, list[QAFinding]] = {}
        for finding in all_findings:
            sig = _signature(finding, self.target_root)
            buckets.setdefault(sig, []).append(finding)

        # Sort clusters: severity (desc) → cluster size (desc) → first source
        def _sort_key(cluster: list[QAFinding]) -> tuple:
            severity_order = {
                "critical": 0,
                "high": 1,
                "medium": 2,
                "low": 3,
                "info": 4,
            }
            top = cluster[0]
            return (
                severity_order.get(top.severity.value, 9),
                -len(cluster),
                top.first_seen_by or "",
            )

        clusters = list(buckets.values())
        clusters.sort(key=_sort_key)
        return clusters
