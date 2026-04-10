"""Judge synthesis for QA ensemble runs.

The synthesizer takes the union of clustered findings from all gladiators and
asks the judge model to:

  1. validate each cluster (split bad clusters where signature collisions
     accidentally merged unrelated bugs)
  2. write the canonical bug text by merging the best phrasing from each
     gladiator's evidence
  3. compute per-gladiator contribution metrics (reproduced count, novel
     count, severity-weighted score)

The output is a single canonical QASynthesisReport plus a Markdown rendering
that becomes the deliverable artifact.

Crucially, this is a *cooperative* judge — there is no winner. The metrics
are diagnostic only and the overall summary should describe the quality of
the ensemble's coverage rather than declaring a champion.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from colosseum.core.config import (
    QA_DEFAULT_JUDGE_BUDGET_USD,
    QA_FINDING_SEVERITY_WEIGHTS,
)
from colosseum.core.models import (
    BudgetPolicy,
    ExperimentRun,
    JudgeConfig,
    JudgeMode,
    ProviderConfig,
    QACreateRequest,
    QAFinding,
    QAFindingSeverity,
    QAFindingStatus,
    QAGladiatorOutcome,
    QASynthesisReport,
    RunStatus,
    TaskSpec,
    TaskType,
)
from colosseum.services.provider_runtime import ProviderRuntimeService

logger = logging.getLogger("colosseum.qa.synthesizer")


def _safe_severity(value: str) -> QAFindingSeverity:
    try:
        return QAFindingSeverity(value)
    except ValueError:
        return QAFindingSeverity.MEDIUM


def _safe_status(value: str) -> QAFindingStatus:
    try:
        return QAFindingStatus(value)
    except ValueError:
        return QAFindingStatus.REPRODUCED


class QAReportSynthesizer:
    """Drive the judge LLM to merge gladiator findings into a canonical report."""

    def __init__(self, provider_runtime: ProviderRuntimeService) -> None:
        self.provider_runtime = provider_runtime

    async def synthesize(
        self,
        run_id: str,
        request: QACreateRequest,
        outcomes: list[QAGladiatorOutcome],
        clusters: list[list[QAFinding]],
        judge_provider: ProviderConfig | None,
    ) -> QASynthesisReport:
        # Heuristic baseline (used as a fallback and to seed contributions)
        contributions = self._compute_contributions(outcomes, clusters)
        canonical_findings = self._heuristic_canonical(clusters)

        report = QASynthesisReport(
            run_id=run_id,
            target_description=request.target_description,
            target_path=request.target_path,
            qa_args=request.qa_args,
            canonical_findings=canonical_findings,
            cluster_count=len(clusters),
            gladiator_contributions=contributions,
            overall_summary=self._heuristic_summary(outcomes, clusters, canonical_findings),
            coverage_notes="",
            synthesizer_model="(heuristic)",
            total_cost_usd=0.0,
        )

        if judge_provider is None:
            return report

        try:
            ai_report = await self._ai_synthesize(
                run_id=run_id,
                request=request,
                outcomes=outcomes,
                clusters=clusters,
                contributions=contributions,
                provider=judge_provider,
            )
            if ai_report is not None:
                return ai_report
        except Exception:
            logger.warning(
                "AI QA synthesis failed, falling back to heuristic", exc_info=True
            )
        return report

    # ── heuristic baseline ──────────────────────────────────────────

    def _heuristic_canonical(self, clusters: list[list[QAFinding]]) -> list[QAFinding]:
        canonical: list[QAFinding] = []
        for cluster in clusters:
            if not cluster:
                continue
            seed = cluster[0]
            sources: list[str] = []
            for finding in cluster:
                for src in finding.sources:
                    if src not in sources:
                        sources.append(src)
            merged_symptom = max(
                (f.symptom for f in cluster if f.symptom),
                key=len,
                default=seed.symptom,
            )
            merged_repro = max(
                (f.reproduction for f in cluster if f.reproduction),
                key=len,
                default=seed.reproduction,
            )
            merged_error = max(
                (f.error_evidence for f in cluster if f.error_evidence),
                key=len,
                default=seed.error_evidence,
            )
            merged_root = max(
                (f.root_cause for f in cluster if f.root_cause),
                key=len,
                default=seed.root_cause,
            )
            canonical.append(
                QAFinding(
                    title=seed.title,
                    symptom=merged_symptom,
                    reproduction=merged_repro,
                    error_evidence=merged_error,
                    root_cause=merged_root,
                    file_path=seed.file_path,
                    line_hint=seed.line_hint,
                    severity=seed.severity,
                    status=seed.status,
                    sources=sources,
                    raw_bug_id=seed.raw_bug_id,
                    first_seen_by=seed.first_seen_by,
                )
            )
        return canonical

    def _compute_contributions(
        self, outcomes: list[QAGladiatorOutcome], clusters: list[list[QAFinding]]
    ) -> dict[str, dict[str, float]]:
        contributions: dict[str, dict[str, float]] = {}
        for outcome in outcomes:
            contributions[outcome.gladiator_id] = {
                "reproduced_count": 0.0,
                "novel_count": 0.0,
                "severity_score": 0.0,
                "total_findings": float(len(outcome.parsed_findings)),
            }
        for cluster in clusters:
            sources_in_cluster = set()
            for finding in cluster:
                sources_in_cluster.update(finding.sources)
            cluster_severity = QA_FINDING_SEVERITY_WEIGHTS.get(
                cluster[0].severity.value, 1.0
            )
            for src in sources_in_cluster:
                bucket = contributions.setdefault(
                    src,
                    {
                        "reproduced_count": 0.0,
                        "novel_count": 0.0,
                        "severity_score": 0.0,
                        "total_findings": 0.0,
                    },
                )
                if cluster[0].status == QAFindingStatus.REPRODUCED:
                    bucket["reproduced_count"] += 1.0
                if len(sources_in_cluster) == 1:
                    bucket["novel_count"] += 1.0
                bucket["severity_score"] += cluster_severity
        return contributions

    def _heuristic_summary(
        self,
        outcomes: list[QAGladiatorOutcome],
        clusters: list[list[QAFinding]],
        canonical: list[QAFinding],
    ) -> str:
        gladiator_count = len(outcomes)
        completed = sum(
            1 for o in outcomes if o.status.value in ("completed", "report_written")
        )
        total_raw = sum(len(o.parsed_findings) for o in outcomes)
        critical = sum(1 for f in canonical if f.severity == QAFindingSeverity.CRITICAL)
        high = sum(1 for f in canonical if f.severity == QAFindingSeverity.HIGH)
        return (
            f"Colosseum QA ensemble: {completed}/{gladiator_count} gladiators completed. "
            f"Aggregated {total_raw} raw findings into {len(clusters)} clusters "
            f"({critical} critical, {high} high)."
        )

    # ── AI synthesis ────────────────────────────────────────────────

    async def _ai_synthesize(
        self,
        run_id: str,
        request: QACreateRequest,
        outcomes: list[QAGladiatorOutcome],
        clusters: list[list[QAFinding]],
        contributions: dict[str, dict[str, float]],
        provider: ProviderConfig,
    ) -> QASynthesisReport | None:
        prompt = build_synthesis_prompt(request, outcomes, clusters, contributions)

        synthetic_run = ExperimentRun(
            project_name="Colosseum QA Synthesis",
            task=TaskSpec(
                title=f"QA synthesis: {request.target_description}",
                problem_statement="Aggregate QA gladiator findings",
                task_type=TaskType.TECHNICAL_REVIEW,
            ),
            agents=[],
            judge=JudgeConfig(mode=JudgeMode.AUTOMATED),
            status=RunStatus.PENDING,
            budget_policy=BudgetPolicy(),
        )

        execution = await self.provider_runtime.execute(
            run=synthetic_run,
            actor_id="qa_synthesizer",
            actor_label="QA Synthesizer",
            provider_config=provider,
            operation="qa_synthesis",
            instructions=prompt,
            metadata={"qa_run_id": run_id},
        )

        raw = (execution.result.content or "").strip()
        usage = execution.result.usage
        canonical, summary, coverage = parse_synthesis_response(raw, clusters)
        if not canonical:
            return None

        return QASynthesisReport(
            run_id=run_id,
            target_description=request.target_description,
            target_path=request.target_path,
            qa_args=request.qa_args,
            canonical_findings=canonical,
            cluster_count=len(clusters),
            gladiator_contributions=contributions,
            overall_summary=summary,
            coverage_notes=coverage,
            synthesizer_model=provider.model,
            total_cost_usd=usage.estimated_cost_usd,
            judge_raw_response=raw[:8000],
        )


def build_synthesis_prompt(
    request: QACreateRequest,
    outcomes: list[QAGladiatorOutcome],
    clusters: list[list[QAFinding]],
    contributions: dict[str, dict[str, float]],
) -> str:
    """Compose the judge prompt — clusters as JSON + bug bodies appended."""
    gladiator_lines: list[str] = []
    for outcome in outcomes:
        gladiator_lines.append(
            f"- {outcome.gladiator_id} ({outcome.display_name}, {outcome.model}): "
            f"status={outcome.status.value}, parse_status={outcome.parse_status}, "
            f"raw_findings={len(outcome.parsed_findings)}, "
            f"cost=${outcome.cost_usd:.4f}, "
            f"tokens={outcome.token_usage.get('total_tokens', 0)}"
        )

    cluster_json: list[dict] = []
    for idx, cluster in enumerate(clusters):
        seed = cluster[0]
        cluster_json.append(
            {
                "cluster_id": idx,
                "title": seed.title,
                "severity": seed.severity.value,
                "file_path": seed.file_path,
                "line_hint": seed.line_hint,
                "raw_bug_id": seed.raw_bug_id,
                "sources": sorted({src for f in cluster for src in f.sources}),
                "members": len(cluster),
                "symptom_preview": (seed.symptom or "")[:280],
                "status": seed.status.value,
            }
        )

    bug_bodies: list[str] = []
    for idx, cluster in enumerate(clusters):
        bug_bodies.append(f"### CLUSTER {idx}")
        for finding in cluster:
            bug_bodies.append(
                "\n".join(
                    [
                        f"  source: {','.join(finding.sources)}",
                        f"  title: {finding.title}",
                        f"  severity: {finding.severity.value}",
                        f"  file: {finding.file_path}:{finding.line_hint}",
                        f"  symptom: {finding.symptom[:600]}",
                        f"  reproduction: {finding.reproduction[:600]}",
                        f"  error: {finding.error_evidence[:600]}",
                        f"  root_cause: {finding.root_cause[:600]}",
                    ]
                )
            )
            bug_bodies.append("")

    return (
        "You are the QA Synthesizer for Colosseum's QA ensemble mode.\n"
        "\n"
        "Your job is NOT to declare a winning gladiator. This is a cooperative QA pass.\n"
        "Multiple gladiators ran the same QA skill against the same target in parallel,\n"
        "each on a disjoint slice of GPUs. Their reports must now be merged into ONE\n"
        "canonical, deduplicated, severity-ranked, REPRODUCED-only QA report.\n"
        "\n"
        f"TARGET: {request.target_description}\n"
        f"TARGET PATH: {request.target_path}\n"
        f"SCOPE ARGS: {request.qa_args or '(none)'}\n"
        "\n"
        "GLADIATORS:\n"
        + "\n".join(gladiator_lines)
        + "\n\n"
        "PRE-CLUSTERED FINDINGS (by signature: file+line bucket+severity+symptom hash):\n"
        + json.dumps(cluster_json, indent=2, ensure_ascii=False)
        + "\n\n"
        "FULL BUG BODIES PER CLUSTER:\n"
        + "\n".join(bug_bodies)
        + "\n\n"
        "═══════════════════════════════════════════════════════════════════════\n"
        "OUTPUT FORMAT — return strictly the JSON object below, nothing else:\n"
        "═══════════════════════════════════════════════════════════════════════\n"
        "\n"
        "```json\n"
        "{\n"
        '  "overall_summary": "<2-4 sentences describing what was tested, what was found, '
        'and whether coverage was adequate>",\n'
        '  "coverage_notes": "<gaps, areas not tested, important blind spots>",\n'
        '  "canonical_findings": [\n'
        "    {\n"
        '      "cluster_ids": [<input cluster ids merged into this canonical bug>],\n'
        '      "title": "<one-line bug title>",\n'
        '      "symptom": "<merged best phrasing>",\n'
        '      "reproduction": "<exact config + command>",\n'
        '      "error_evidence": "<actual traceback or measured numbers>",\n'
        '      "root_cause": "<analysis>",\n'
        '      "file_path": "<file>",\n'
        '      "line_hint": <int or null>,\n'
        '      "severity": "critical|high|medium|low|info",\n'
        '      "status": "reproduced|unverified|false_positive",\n'
        '      "sources": ["<gladiator_id>", ...]\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "```\n"
        "\n"
        "Rules:\n"
        "- Drop FALSE_POSITIVE findings from canonical_findings.\n"
        "- Split a cluster into multiple canonical findings if you decide it accidentally\n"
        "  merged unrelated bugs (use multiple cluster_ids only when truly the same bug).\n"
        "- Deduplicate aggressively but never invent bugs that no gladiator reported.\n"
        "- Sort canonical_findings by severity desc, then by source count desc.\n"
        "- Be concise. Drop fluff. The user wants a usable bug list, not prose.\n"
    )


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse_synthesis_response(
    raw: str, clusters: list[list[QAFinding]]
) -> tuple[list[QAFinding], str, str]:
    """Parse the synthesizer's JSON response into QAFinding objects."""
    if not raw:
        return [], "", ""

    blob = None
    match = _JSON_BLOCK_RE.search(raw)
    if match:
        blob = match.group(1)
    else:
        # Fallback: locate first balanced { ... } in the raw text.
        start = raw.find("{")
        if start >= 0:
            depth = 0
            in_str = False
            esc = False
            end = -1
            for i in range(start, len(raw)):
                ch = raw[i]
                if in_str:
                    if esc:
                        esc = False
                        continue
                    if ch == "\\":
                        esc = True
                        continue
                    if ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            if end > 0:
                blob = raw[start:end]

    if not blob:
        return [], "", ""

    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return [], "", ""

    if not isinstance(data, dict):
        return [], "", ""

    summary = str(data.get("overall_summary") or "").strip()
    coverage = str(data.get("coverage_notes") or "").strip()
    findings_raw = data.get("canonical_findings") or []
    if not isinstance(findings_raw, list):
        return [], summary, coverage

    canonical: list[QAFinding] = []
    for entry in findings_raw:
        if not isinstance(entry, dict):
            continue
        try:
            line_hint_raw = entry.get("line_hint")
            line_hint = int(line_hint_raw) if line_hint_raw not in (None, "") else None
        except (TypeError, ValueError):
            line_hint = None
        sources_raw = entry.get("sources") or []
        sources = [str(s) for s in sources_raw if s]
        cluster_ids_raw = entry.get("cluster_ids") or []
        # Backfill sources from referenced clusters when the model omitted them.
        if not sources and isinstance(cluster_ids_raw, list):
            for cid in cluster_ids_raw:
                try:
                    cidx = int(cid)
                except (TypeError, ValueError):
                    continue
                if 0 <= cidx < len(clusters):
                    for f in clusters[cidx]:
                        for src in f.sources:
                            if src not in sources:
                                sources.append(src)
        canonical.append(
            QAFinding(
                title=str(entry.get("title") or "(untitled)"),
                symptom=str(entry.get("symptom") or ""),
                reproduction=str(entry.get("reproduction") or ""),
                error_evidence=str(entry.get("error_evidence") or ""),
                root_cause=str(entry.get("root_cause") or ""),
                file_path=str(entry.get("file_path") or "") or None,
                line_hint=line_hint,
                severity=_safe_severity(str(entry.get("severity") or "medium")),
                status=_safe_status(str(entry.get("status") or "reproduced")),
                sources=sources,
            )
        )
    return canonical, summary, coverage


def render_markdown_report(report: QASynthesisReport, gladiators: list[QAGladiatorOutcome]) -> str:
    """Render the canonical synthesized QA report as Markdown."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    severity_counts: dict[str, int] = {
        "critical": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
        "info": 0,
    }
    for f in report.canonical_findings:
        severity_counts[f.severity.value] = severity_counts.get(f.severity.value, 0) + 1

    lines: list[str] = []
    lines.append(f"# Colosseum QA Report — {now}")
    lines.append("")
    lines.append("## Summary")
    lines.append(f"- **Target**: {report.target_description}")
    lines.append(f"- **Target path**: `{report.target_path}`")
    lines.append(f"- **Scope args**: `{report.qa_args or '(none)'}`")
    lines.append(f"- **Gladiators**: {len(gladiators)}")
    lines.append(f"- **Clusters**: {report.cluster_count}")
    lines.append(
        f"- **Canonical findings**: {len(report.canonical_findings)} "
        f"(critical={severity_counts['critical']}, high={severity_counts['high']}, "
        f"medium={severity_counts['medium']}, low={severity_counts['low']}, "
        f"info={severity_counts['info']})"
    )
    lines.append(f"- **Total cost**: ${report.total_cost_usd:.4f}")
    lines.append(f"- **Synthesizer**: {report.synthesizer_model}")
    lines.append("")
    if report.overall_summary:
        lines.append(report.overall_summary)
        lines.append("")
    if report.coverage_notes:
        lines.append("### Coverage notes")
        lines.append(report.coverage_notes)
        lines.append("")

    lines.append("## Gladiator Contributions")
    lines.append("")
    lines.append("| Gladiator | Status | Reproduced | Novel | Severity Score | Cost |")
    lines.append("|-----------|--------|------------|-------|----------------|------|")
    for outcome in gladiators:
        contrib = report.gladiator_contributions.get(outcome.gladiator_id, {})
        lines.append(
            f"| {outcome.display_name} ({outcome.gladiator_id}) "
            f"| {outcome.status.value} "
            f"| {int(contrib.get('reproduced_count', 0))} "
            f"| {int(contrib.get('novel_count', 0))} "
            f"| {contrib.get('severity_score', 0):.1f} "
            f"| ${outcome.cost_usd:.4f} |"
        )
    lines.append("")

    lines.append("## Confirmed Bugs (Reproduced)")
    lines.append("")
    if not report.canonical_findings:
        lines.append("_No reproduced bugs across the gladiator ensemble._")
    else:
        for idx, finding in enumerate(report.canonical_findings, start=1):
            lines.append(
                f"### {idx}. [{finding.severity.value.upper()}] {finding.title}"
            )
            sources = ", ".join(finding.sources) if finding.sources else "(unknown)"
            lines.append(f"- **Sources**: {sources}")
            if finding.file_path:
                loc = f"`{finding.file_path}`"
                if finding.line_hint:
                    loc = f"`{finding.file_path}:{finding.line_hint}`"
                lines.append(f"- **Location**: {loc}")
            if finding.symptom:
                lines.append(f"- **Symptom**: {finding.symptom}")
            if finding.reproduction:
                lines.append(f"- **Reproduction**: {finding.reproduction}")
            if finding.error_evidence:
                lines.append(f"- **Error evidence**: {finding.error_evidence}")
            if finding.root_cause:
                lines.append(f"- **Root cause**: {finding.root_cause}")
            lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"_Generated by Colosseum QA ensemble — run {report.run_id}_")
    return "\n".join(lines) + "\n"
