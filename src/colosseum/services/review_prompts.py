"""Prompt templates for multi-phase code review."""

from __future__ import annotations

from colosseum.core.config import REVIEW_PHASE_CONFIG
from colosseum.core.models import ReviewPhase


def build_review_problem_statement(
    phase: ReviewPhase,
    target_description: str,
    criteria: list[str],
    rules_context: str | None = None,
    git_diff: str | None = None,
    response_language: str = "auto",
) -> str:
    """Build the problem statement for a review phase mini-debate."""
    config = REVIEW_PHASE_CONFIG[phase.value]
    label = config["label"]

    parts: list[str] = []

    # Language instruction
    lang = response_language if response_language and response_language != "auto" else ""
    if lang:
        parts.append(
            f"MANDATORY LANGUAGE: You MUST write your ENTIRE response in {lang}. "
            f"Every field, every section, every sentence must be in {lang}."
        )

    parts.append(f"CODE REVIEW — {label}")
    parts.append(f"Review target: {target_description}")

    parts.append("You are performing a structured code review. Your task is to:")
    parts.append(
        "1. Analyze the provided code against the review criteria below.\n"
        "2. Identify specific findings with severity levels.\n"
        "3. Provide actionable recommendations for each finding.\n"
        "4. Debate with the other reviewer(s) to reach consensus on findings."
    )

    parts.append("REVIEW CRITERIA:")
    for i, criterion in enumerate(criteria, 1):
        parts.append(f"  {i}. {criterion}")

    if rules_context:
        parts.append(f"PROJECT RULES AND CONVENTIONS:\n{rules_context}")

    if git_diff:
        diff_truncated = git_diff[:8000]
        if len(git_diff) > 8000:
            diff_truncated += "\n...[DIFF TRUNCATED]"
        parts.append(f"GIT DIFF (recent changes):\n```diff\n{diff_truncated}\n```")

    parts.append(
        "OUTPUT FORMAT: Structure your review as a plan with these sections:\n"
        "- summary: Brief overview of findings for this review phase\n"
        "- evidence_basis: Specific code references supporting your findings\n"
        "- strengths: What the code does well in this area\n"
        "- weaknesses: Issues found, each with severity (critical/high/medium/low/info)\n"
        "- risks: Potential problems with severity and mitigation\n"
        "- implementation_strategy: Recommended fixes or improvements\n"
        "- trade_offs: Tradeoffs of suggested changes"
    )

    if lang:
        parts.append(
            f"REMINDER: Your response MUST be entirely in {lang}. No other language permitted."
        )

    return "\n\n".join(parts)


def build_finding_extraction_prompt(phase: ReviewPhase) -> str:
    """Build a prompt for extracting structured findings from debate output."""
    config = REVIEW_PHASE_CONFIG[phase.value]
    return (
        f"Extract code review findings from the {config['label']} debate.\n"
        "For each finding, identify:\n"
        "- title: Short description\n"
        "- severity: critical, high, medium, low, or info\n"
        "- description: Detailed explanation\n"
        "- file_path: Affected file (if identifiable)\n"
        "- line_range: Affected lines (if identifiable)\n"
        "- recommendation: How to fix\n"
        "- agent_consensus: How much the reviewers agreed (0.0-1.0)"
    )
