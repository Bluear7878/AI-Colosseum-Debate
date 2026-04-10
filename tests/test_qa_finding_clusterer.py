"""Unit tests for QAFindingClusterer signature-based bucketing."""

from __future__ import annotations

from colosseum.core.models import (
    ProviderType,
    QAFinding,
    QAFindingSeverity,
    QAGladiatorOutcome,
    QAGladiatorStatus,
)
from colosseum.services.qa_finding_clusterer import QAFindingClusterer


def _outcome(gid: str, findings: list[QAFinding]) -> QAGladiatorOutcome:
    return QAGladiatorOutcome(
        gladiator_id=gid,
        display_name=gid.title(),
        provider_type=ProviderType.CLAUDE_CLI,
        model="claude-sonnet-4-6",
        status=QAGladiatorStatus.REPORT_WRITTEN,
        parsed_findings=findings,
    )


def _finding(
    gid: str,
    title: str,
    file_path: str,
    line: int | None,
    severity: QAFindingSeverity = QAFindingSeverity.HIGH,
    symptom: str = "",
) -> QAFinding:
    return QAFinding(
        title=title,
        symptom=symptom or title,
        file_path=file_path,
        line_hint=line,
        severity=severity,
        sources=[gid],
        first_seen_by=gid,
    )


def test_two_gladiators_same_bug_collapse_into_one_cluster():
    f1 = _finding("alpha", "Login empty password ValueError", "schema.py", 123)
    f2 = _finding("beta", "Login empty password ValueError", "schema.py", 123)
    clusterer = QAFindingClusterer()
    clusters = clusterer.cluster([_outcome("alpha", [f1]), _outcome("beta", [f2])])
    assert len(clusters) == 1
    assert {f.first_seen_by for f in clusters[0]} == {"alpha", "beta"}


def test_near_line_findings_in_same_bucket():
    """Lines 142 and 147 are within the same 10-line bucket."""
    f1 = _finding("alpha", "Same bug X", "module.py", 142)
    f2 = _finding("beta", "Same bug X", "module.py", 147)
    clusterer = QAFindingClusterer()
    clusters = clusterer.cluster([_outcome("alpha", [f1]), _outcome("beta", [f2])])
    assert len(clusters) == 1


def test_different_files_kept_separate():
    f1 = _finding("alpha", "ValueError", "a.py", 10)
    f2 = _finding("beta", "ValueError", "b.py", 10)
    clusterer = QAFindingClusterer()
    clusters = clusterer.cluster([_outcome("alpha", [f1]), _outcome("beta", [f2])])
    assert len(clusters) == 2


def test_severity_mismatch_kept_separate():
    f1 = _finding(
        "alpha", "Same title", "x.py", 100, severity=QAFindingSeverity.CRITICAL
    )
    f2 = _finding("beta", "Same title", "x.py", 100, severity=QAFindingSeverity.LOW)
    clusterer = QAFindingClusterer()
    clusters = clusterer.cluster([_outcome("alpha", [f1]), _outcome("beta", [f2])])
    assert len(clusters) == 2


def test_clusters_sorted_critical_first():
    f_low = _finding("a", "Low bug", "low.py", 1, severity=QAFindingSeverity.LOW)
    f_crit = _finding("a", "Crit bug", "crit.py", 1, severity=QAFindingSeverity.CRITICAL)
    f_high = _finding("a", "High bug", "high.py", 1, severity=QAFindingSeverity.HIGH)
    clusterer = QAFindingClusterer()
    clusters = clusterer.cluster([_outcome("a", [f_low, f_crit, f_high])])
    assert len(clusters) == 3
    assert clusters[0][0].severity == QAFindingSeverity.CRITICAL
    assert clusters[1][0].severity == QAFindingSeverity.HIGH
    assert clusters[2][0].severity == QAFindingSeverity.LOW


def test_traceback_address_normalization_collapses_clusters():
    """Tracebacks with different memory addresses should still cluster."""
    f1 = _finding(
        "alpha",
        "Crash",
        "x.py",
        10,
        symptom="Crash at 0x7f8a1234abcd",
    )
    f2 = _finding(
        "beta",
        "Crash",
        "x.py",
        10,
        symptom="Crash at 0x7f8b5678ffff",
    )
    clusterer = QAFindingClusterer()
    clusters = clusterer.cluster([_outcome("alpha", [f1]), _outcome("beta", [f2])])
    # Same file/line/severity → should collapse even with different addrs
    assert len(clusters) == 1
    assert len(clusters[0]) == 2


def test_empty_outcomes_returns_empty_list():
    clusterer = QAFindingClusterer()
    assert clusterer.cluster([]) == []
    assert clusterer.cluster([_outcome("alpha", [])]) == []


def test_no_line_hint_does_not_crash():
    f = _finding("a", "no-line bug", "thing.py", None)
    clusterer = QAFindingClusterer()
    clusters = clusterer.cluster([_outcome("a", [f])])
    assert len(clusters) == 1
