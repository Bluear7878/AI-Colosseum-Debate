"""Unit tests for QAReportParser layered, fail-soft extraction."""

from __future__ import annotations

from colosseum.core.models import QAFindingSeverity, QAFindingStatus
from colosseum.services.qa_report_parser import QAReportParser


GOLDEN_REPORT = """\
# QA Report — 2026-04-10

## Summary
- Scope: api auth full pass
- Duration: 47 min
- Result: 12 tests passed, 2 failed, 2 confirmed bugs found

## Token Usage
| Agent | Model | Tokens | Duration |
|-------|-------|--------|----------|
| Coordinator | Opus | 24500 | 47m |

## Test Results
| Component | Test | Model | Status | Notes |
|-----------|------|-------|--------|-------|
| auth | login flow | gpt-test | PASS | |

## Confirmed Bugs (Reproduced)

### [NEW] G-017: Login fails with empty password
- **Symptom**: AuthService crashes when password is set to empty string
- **Reproduction**: AuthService.login(user="alice", password="")
- **Error**: ValueError: shape mismatch at schema.py:123
- **Root Cause**: input validator does not handle empty string
- **File**: src/example/schema.py:123
- **Severity**: High

### G-018: Token refresh silent failure
- **Symptom**: Token refresh silently returns None when cache is cold
- **Reproduction**: TokenStore(cache=None).refresh(user_id=42)
- **Error**: AssertionError, no traceback (silent)
- **File**: example.py:412
- **Severity**: critical

## False Positives Filtered
| Candidate | Claimed By | Verdict |
|-----------|-----------|---------|
"""

NO_BUGS_REPORT = """\
# QA Report

## Summary
All passes clean.

## Confirmed Bugs (Reproduced)

_No reproduced bugs found._
"""

DEGRADED_REPORT = """\
# Some report

## Random Header
This report has no Confirmed Bugs section at all but mentions G-042 in passing.

### G-042: parser test bug
- Symptom: something broke
- File: foo.py:99
- Severity: medium
"""

MALFORMED_REPORT = """\
# Half-broken report

## Confirmed Bugs (Reproduced)

### G-001: dangling header with no fields

### G-002: only a title here
- Severity: NOTASEVERITY
"""

KOREAN_REPORT = """\
# QA 리포트 — 2026-04-10

## Confirmed Bugs (Reproduced)

### [신규] G-077: 한글 버그 제목
- **Symptom**: 한글로 작성된 증상 설명입니다
- **Reproduction**: ExampleService(...)
- **Error**: ValueError가 발생함
- **File**: src/foo.py:42
- **Severity**: 높음
"""


def test_parses_golden_report_cleanly():
    parser = QAReportParser()
    findings, sections, status = parser.parse(GOLDEN_REPORT, "alpha")
    assert status == "ok"
    assert len(findings) == 2
    titles = [f.title for f in findings]
    assert "Login fails with empty password" in titles[0]
    assert findings[0].raw_bug_id == "G-017"
    assert findings[0].file_path == "src/example/schema.py"
    assert findings[0].line_hint == 123
    assert findings[0].severity == QAFindingSeverity.HIGH
    assert findings[0].sources == ["alpha"]
    assert findings[1].severity == QAFindingSeverity.CRITICAL
    assert "Summary" in sections


def test_no_bugs_report_returns_empty_findings():
    parser = QAReportParser()
    findings, _, status = parser.parse(NO_BUGS_REPORT, "beta")
    assert findings == []
    assert status == "ok"


def test_degraded_report_salvages_bugs():
    parser = QAReportParser()
    findings, _, status = parser.parse(DEGRADED_REPORT, "gamma")
    assert status in ("ok", "degraded")
    assert len(findings) == 1
    assert findings[0].raw_bug_id == "G-042"
    assert findings[0].file_path == "foo.py"
    assert findings[0].line_hint == 99
    assert findings[0].severity == QAFindingSeverity.MEDIUM


def test_malformed_report_does_not_raise():
    parser = QAReportParser()
    findings, _, status = parser.parse(MALFORMED_REPORT, "delta")
    # Both bugs should be salvaged even with degraded fields.
    assert len(findings) >= 1
    assert status in ("ok", "degraded")
    # Unknown severity falls back to medium.
    assert all(f.severity in QAFindingSeverity for f in findings)


def test_korean_report_parses():
    parser = QAReportParser()
    findings, _, status = parser.parse(KOREAN_REPORT, "epsilon")
    assert status == "ok"
    assert len(findings) == 1
    assert findings[0].raw_bug_id == "G-077"
    assert findings[0].file_path == "src/foo.py"
    assert findings[0].line_hint == 42
    assert "한글" in findings[0].symptom


def test_empty_input_returns_skipped():
    parser = QAReportParser()
    findings, _, status = parser.parse("", "zeta")
    assert findings == []
    assert status == "skipped"


def test_finding_status_defaults_to_reproduced():
    parser = QAReportParser()
    findings, _, _ = parser.parse(GOLDEN_REPORT, "alpha")
    assert all(f.status == QAFindingStatus.REPRODUCED for f in findings)


def test_parser_never_raises_on_garbage():
    parser = QAReportParser()
    findings, _, status = parser.parse("@@@%%%###\n\n```binary garbage```", "x")
    assert findings == []
    assert status in ("ok", "skipped", "degraded", "failed")
