"""File-backed persistence for QA ensemble runs.

QA runs have a different artifact layout from debate/review runs:

  .colosseum/qa/<run_id>/
    qa_run.json                 # full QARun pydantic state
    gpu_plan.json               # detected/eligible/allocations snapshot
    synthesized_report.md       # the canonical deliverable (judge output)
    findings.json               # structured canonical findings
    gladiators/
      <gladiator_id>/
        report.md               # raw gladiator report
        output.log              # tee'd stdout
        stderr.log              # tee'd stderr
        stream.jsonl            # raw stream-json events (Claude path)
"""

from __future__ import annotations

import json
from pathlib import Path

from colosseum.core.config import QA_RUN_ROOT
from colosseum.core.models import QARun


class QARunRepository:
    """Persist QARun artifacts under `.colosseum/qa/<run_id>/`."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or QA_RUN_ROOT
        self.root.mkdir(parents=True, exist_ok=True)

    # ── lifecycle ────────────────────────────────────────────────────

    def init_run(self, run: QARun) -> Path:
        """Create the per-run directory tree and return the run dir."""
        run_dir = self.root / run.run_id
        gladiators_dir = run_dir / "gladiators"
        run_dir.mkdir(parents=True, exist_ok=True)
        gladiators_dir.mkdir(parents=True, exist_ok=True)
        for outcome in run.gladiators:
            (gladiators_dir / outcome.gladiator_id).mkdir(parents=True, exist_ok=True)
        self.save_run(run)
        return run_dir

    def gladiator_dir(self, run_id: str, gladiator_id: str) -> Path:
        return self.root / run_id / "gladiators" / gladiator_id

    def run_dir(self, run_id: str) -> Path:
        return self.root / run_id

    # ── persistence ──────────────────────────────────────────────────

    def save_run(self, run: QARun) -> None:
        run_dir = self.root / run.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        self._write_json(run_dir / "qa_run.json", run.model_dump(mode="json"))
        self._write_json(run_dir / "gpu_plan.json", run.gpu_plan.model_dump(mode="json"))
        if run.synthesis is not None:
            self._write_json(
                run_dir / "findings.json",
                {
                    "run_id": run.run_id,
                    "canonical_findings": [
                        f.model_dump(mode="json") for f in run.synthesis.canonical_findings
                    ],
                    "gladiator_contributions": run.synthesis.gladiator_contributions,
                    "cluster_count": run.synthesis.cluster_count,
                },
            )

    def save_synthesized_markdown(self, run_id: str, markdown: str) -> Path:
        run_dir = self.root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / "synthesized_report.md"
        path.write_text(markdown, encoding="utf-8")
        return path

    def save_gladiator_log(
        self, run_id: str, gladiator_id: str, name: str, content: str
    ) -> Path:
        gd = self.gladiator_dir(run_id, gladiator_id)
        gd.mkdir(parents=True, exist_ok=True)
        path = gd / name
        path.write_text(content, encoding="utf-8")
        return path

    def load_run(self, run_id: str) -> QARun:
        path = self.root / run_id / "qa_run.json"
        if not path.exists():
            matches = sorted(self.root.glob(f"{run_id}*/qa_run.json"))
            if not matches:
                raise FileNotFoundError(f"QA run {run_id} does not exist.")
            if len(matches) > 1:
                raise FileNotFoundError(f"QA run prefix {run_id} is ambiguous.")
            path = matches[0]
        return QARun.model_validate_json(path.read_text(encoding="utf-8"))

    def list_runs(self) -> list[QARun]:
        runs: list[QARun] = []
        for path in sorted(self.root.glob("*/qa_run.json"), reverse=True):
            try:
                runs.append(QARun.model_validate_json(path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return runs

    def _write_json(self, path: Path, payload: object) -> None:
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
