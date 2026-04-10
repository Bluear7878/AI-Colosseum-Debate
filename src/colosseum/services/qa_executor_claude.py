"""Real Claude Code subprocess executor for QA gladiators.

Each Claude QA gladiator runs as a non-interactive `claude --print` subprocess
with the target project as its cwd. The subprocess organically invokes the
`/qa` skill living in `<target>/.claude/skills/qa/`, spawns its own sub-agents
via the Task tool, and writes a final report.md to a path Colosseum specifies.

Critical design points (also documented in plans/humble-floating-sundae.md):

* Prompt is passed as the **positional argument** to `claude`, never via stdin.
  Stdin is reserved for `--input-format stream-json`, which must not be mixed.
* `cwd` must be the target project directory; the QA skill is resolved relative
  to cwd. There is no `--cwd` flag.
* `--add-dir <run_dir>` is required so the subprocess can write report.md into
  Colosseum's per-gladiator directory (which lives outside the target).
* `--output-format stream-json` requires `--verbose`.
* `--max-budget-usd` is the strongest single safeguard against runaway runs.
* `--no-session-persistence` keeps test runs out of `~/.claude/projects/`.
* `--allowedTools "Bash Read Write Edit Glob Grep Task"` is required so the
  coordinator can spawn sub-agents (`Task`) and run quantization (`Bash`).
* `CUDA_VISIBLE_DEVICES` is set in the subprocess env. The coordinator is told
  in the prompt to never override it when spawning children.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import uuid4

from colosseum.core.config import (
    QA_DEFAULT_MAX_BUDGET_USD_PER_GLADIATOR,
    QA_DEFAULT_MAX_GLADIATOR_MINUTES,
    QA_DEFAULT_STALL_TIMEOUT_MINUTES,
)
from colosseum.core.models import (
    AgentConfig,
    QAGladiatorOutcome,
    QAGladiatorStatus,
)


# Tools the QA skill needs inside the gladiator subprocess. `Task` lets the
# coordinator spawn its own sub-agents — without it, /qa cannot run as designed.
QA_ALLOWED_TOOLS = "Bash Read Write Edit Glob Grep Task TodoWrite WebFetch WebSearch"


@dataclass
class StreamReaderResult:
    """Aggregate metrics extracted while reading the stream-json output."""

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_creation_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cost_usd: float = 0.0
    final_result_text: str | None = None
    last_event_at: float = field(default_factory=time.monotonic)
    event_count: int = 0
    error_messages: list[str] = field(default_factory=list)
    saw_result: bool = False

    def total_tokens(self) -> int:
        return (
            self.total_input_tokens
            + self.total_output_tokens
            + self.total_cache_creation_tokens
            + self.total_cache_read_tokens
        )


def build_qa_prompt(
    qa_args: str,
    report_path: Path,
    output_root: Path,
    assigned_gpus: list[int],
    brief: bool,
    spec: str | None,
    keep_bug_outputs: bool,
    response_language: str,
    gladiator_id: str,
    run_id: str,
) -> str:
    """Build the positional prompt sent to `claude --print`.

    The prompt has two halves: an explicit `/qa <args>` invocation that boots
    the skill, plus a Colosseum override block that redirects all path-writes
    away from the target repo and pins the GPU assignment.
    """
    args = qa_args.strip()
    if brief and "--brief" not in args:
        args = (args + " --brief").strip()
    if spec and "--spec" not in args:
        args = (args + f" --spec {spec}").strip()
    if assigned_gpus:
        gpu_csv = ",".join(str(i) for i in assigned_gpus)
        if "--gpus" not in args:
            args = (args + f" --gpus {gpu_csv}").strip()

    gpus_display = ",".join(str(i) for i in assigned_gpus) if assigned_gpus else "(none/--brief)"
    retention = "keep bug outputs" if keep_bug_outputs else "delete all"
    lang_line = (
        f"Respond in language: {response_language}.\n"
        if response_language and response_language != "auto"
        else ""
    )

    return (
        f"/qa {args}\n"
        "\n"
        "═══════════════════════════════════════════════════════════════════════\n"
        "COLOSSEUM QA ORCHESTRATOR — overrides for this gladiator run\n"
        "═══════════════════════════════════════════════════════════════════════\n"
        "\n"
        "You are running as a single gladiator inside a Colosseum QA ensemble.\n"
        "Multiple gladiators are testing the same target in parallel, each on a\n"
        "disjoint slice of GPUs. Their reports will be merged afterwards by a\n"
        "judge — this is a collaborative QA pass, not a competition.\n"
        "\n"
        f"COLOSSEUM_QA_RUN_ID: {run_id}\n"
        f"COLOSSEUM_QA_GLADIATOR_ID: {gladiator_id}\n"
        f"COLOSSEUM_QA_REPORT_PATH: {report_path}\n"
        f"COLOSSEUM_QA_OUTPUT_ROOT: {output_root}\n"
        "COLOSSEUM_QA_GOTCHAS_UPDATE: disabled\n"
        f"ASSIGNED GPUS (CUDA_VISIBLE_DEVICES): [{gpus_display}]\n"
        f"OUTPUT RETENTION: {retention}\n"
        "\n"
        "STRICT RULES — these override the /qa skill's defaults:\n"
        "\n"
        "1. WRITE THE FINAL REPORT TO COLOSSEUM_QA_REPORT_PATH.\n"
        "   When the /qa skill instructs you to write the report to\n"
        "   `QA/reports/{date}-{scope}.md`, you MUST instead use the absolute\n"
        "   path in COLOSSEUM_QA_REPORT_PATH above. Use the Write tool. Do NOT\n"
        "   create or write any file under the target's QA/reports/ directory.\n"
        "\n"
        "2. USE COLOSSEUM_QA_OUTPUT_ROOT FOR ALL SCRATCH/EVIDENCE OUTPUTS.\n"
        "   When the skill says to use /tmp/qa_*/, use COLOSSEUM_QA_OUTPUT_ROOT\n"
        "   instead so multiple gladiators don't collide on the same path.\n"
        "\n"
        "3. DO NOT MODIFY ANY FILE INSIDE THE TARGET REPOSITORY.\n"
        "   In particular: do NOT append to QA/gotchas.md, do NOT touch\n"
        "   QA/bookmark.md, do NOT update QA/aq.md or QA/gq.md. Read them as\n"
        "   needed but never write to them. Colosseum aggregates findings\n"
        "   across all gladiators and writes back after the run.\n"
        "\n"
        "4. RESPECT THE GPU ASSIGNMENT.\n"
        "   You have been started with CUDA_VISIBLE_DEVICES set to your slice.\n"
        "   When you spawn sub-agents (Task tool), you MUST NOT override\n"
        "   CUDA_VISIBLE_DEVICES in their environment. Every sub-agent and\n"
        "   every Bash invocation must inherit your CUDA_VISIBLE_DEVICES value\n"
        "   unchanged. Never set it to 'all' or '0,1,2,3,4,5,6,7'. If you need\n"
        "   to target a specific GPU, pick from your assigned slice only.\n"
        "\n"
        "5. PRODUCE A REAL REPRODUCED-ONLY REPORT.\n"
        "   Every bug in the final report must be REPRODUCED via actual code\n"
        "   execution (not CODE_ANALYSIS). Follow the /qa skill's verification\n"
        "   phase rules. False positives belong in the False Positives section\n"
        "   or excluded entirely.\n"
        "\n"
        f"{lang_line}"
        "Begin by reading QA/bookmark.md (if present) to orient yourself, then\n"
        "follow the /qa skill's interview-and-execute flow. Skip steps that are\n"
        "already implied by the args above.\n"
    )


class ClaudeStreamReader:
    """Parse `claude --print --output-format stream-json --verbose` output.

    Each line of stdout is one JSON event:
      * {"type": "system", ...}    — session info, init events
      * {"type": "assistant", ...} — partial or final assistant messages
      * {"type": "user", ...}      — tool result events
      * {"type": "result", ...}    — final terminator event with usage/cost

    The reader tees raw lines to `<gladiator_dir>/stream.jsonl` for post-mortem
    analysis and updates a `StreamReaderResult` aggregate that the executor
    inspects after the subprocess exits.
    """

    def __init__(
        self,
        jsonl_path: Path,
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self.jsonl_path = jsonl_path
        self.on_event = on_event
        self.result = StreamReaderResult()

    async def read(self, stream: asyncio.StreamReader) -> StreamReaderResult:
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with self.jsonl_path.open("w", encoding="utf-8") as tee:
            while True:
                try:
                    line = await stream.readline()
                except Exception as exc:
                    self.result.error_messages.append(f"stream read failure: {exc}")
                    break
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\n")
                if not text.strip():
                    continue
                tee.write(text + "\n")
                tee.flush()
                self.result.last_event_at = time.monotonic()
                self.result.event_count += 1
                try:
                    event = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                self._absorb(event)
                if self.on_event is not None:
                    try:
                        await self.on_event(event)
                    except Exception:
                        pass
        return self.result

    def _absorb(self, event: dict[str, Any]) -> None:
        etype = event.get("type", "")
        if etype == "result":
            self.result.saw_result = True
            usage = event.get("usage") or {}
            self.result.total_input_tokens += int(usage.get("input_tokens", 0) or 0)
            self.result.total_output_tokens += int(usage.get("output_tokens", 0) or 0)
            self.result.total_cache_creation_tokens += int(
                usage.get("cache_creation_input_tokens", 0) or 0
            )
            self.result.total_cache_read_tokens += int(
                usage.get("cache_read_input_tokens", 0) or 0
            )
            cost = event.get("total_cost_usd") or event.get("cost_usd")
            if cost is not None:
                try:
                    self.result.total_cost_usd = float(cost)
                except (TypeError, ValueError):
                    pass
            res = event.get("result")
            if isinstance(res, str):
                self.result.final_result_text = res
        elif etype == "assistant":
            msg = event.get("message") or {}
            usage = msg.get("usage") or {}
            if usage:
                # Assistant events also report incremental usage. The terminal
                # `result` event holds the authoritative total, so prefer it
                # when present; fall back to per-message usage otherwise.
                if not self.result.saw_result:
                    self.result.total_input_tokens = max(
                        self.result.total_input_tokens, int(usage.get("input_tokens", 0) or 0)
                    )
                    self.result.total_output_tokens += int(usage.get("output_tokens", 0) or 0)
        elif etype == "system":
            sub = event.get("subtype", "")
            if sub == "error":
                msg = str(event.get("message") or "")
                if msg:
                    self.result.error_messages.append(msg)


class ClaudeQAExecutor:
    """Spawn a real `claude --print` subprocess to run the QA skill."""

    def __init__(
        self,
        gladiator_id: str,
        agent_config: AgentConfig,
        target_path: Path,
        gladiator_dir: Path,
        run_id: str,
        qa_args: str,
        assigned_gpus: list[int],
        brief: bool = False,
        spec: str | None = None,
        keep_bug_outputs: bool = False,
        response_language: str = "auto",
        max_budget_usd: float = QA_DEFAULT_MAX_BUDGET_USD_PER_GLADIATOR,
        max_gladiator_minutes: int = QA_DEFAULT_MAX_GLADIATOR_MINUTES,
        stall_timeout_minutes: int = QA_DEFAULT_STALL_TIMEOUT_MINUTES,
        fallback_model: str | None = None,
        claude_binary: str = "claude",
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self.gladiator_id = gladiator_id
        self.agent_config = agent_config
        self.target_path = target_path
        self.gladiator_dir = gladiator_dir
        self.run_id = run_id
        self.qa_args = qa_args
        self.assigned_gpus = assigned_gpus
        self.brief = brief
        self.spec = spec
        self.keep_bug_outputs = keep_bug_outputs
        self.response_language = response_language
        self.max_budget_usd = max_budget_usd
        self.max_gladiator_minutes = max_gladiator_minutes
        self.stall_timeout_minutes = stall_timeout_minutes
        self.fallback_model = fallback_model
        self.claude_binary = claude_binary
        self.on_event = on_event
        self.session_id = str(uuid4())

    # ── public API ──────────────────────────────────────────────────

    async def run(self) -> QAGladiatorOutcome:
        self.gladiator_dir.mkdir(parents=True, exist_ok=True)
        report_path = self.gladiator_dir / "report.md"
        stdout_log = self.gladiator_dir / "output.log"
        stderr_log = self.gladiator_dir / "stderr.log"
        stream_jsonl = self.gladiator_dir / "stream.jsonl"
        output_root = self.gladiator_dir / "scratch"
        output_root.mkdir(parents=True, exist_ok=True)

        outcome = QAGladiatorOutcome(
            gladiator_id=self.gladiator_id,
            display_name=self.agent_config.display_name,
            provider_type=self.agent_config.provider.type,
            model=self.agent_config.provider.model,
            assigned_gpus=list(self.assigned_gpus),
            status=QAGladiatorStatus.RUNNING,
            report_path=str(report_path),
            stdout_log_path=str(stdout_log),
            stderr_log_path=str(stderr_log),
            stream_jsonl_path=str(stream_jsonl),
            session_id=self.session_id,
            started_at=datetime.now(timezone.utc),
        )
        await self._emit("gladiator_started", {"gladiator_id": self.gladiator_id})

        prompt = build_qa_prompt(
            qa_args=self.qa_args,
            report_path=report_path,
            output_root=output_root,
            assigned_gpus=self.assigned_gpus,
            brief=self.brief,
            spec=self.spec,
            keep_bug_outputs=self.keep_bug_outputs,
            response_language=self.response_language,
            gladiator_id=self.gladiator_id,
            run_id=self.run_id,
        )

        command = self._build_command(prompt)
        env = self._build_env()

        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(self.target_path),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            outcome.status = QAGladiatorStatus.FAILED
            outcome.error = f"claude binary not found: {exc}"
            outcome.completed_at = datetime.now(timezone.utc)
            outcome.duration_seconds = 0.0
            await self._emit("gladiator_failed", {"gladiator_id": self.gladiator_id, "error": outcome.error})
            return outcome
        except Exception as exc:
            outcome.status = QAGladiatorStatus.FAILED
            outcome.error = f"failed to spawn claude subprocess: {exc}"
            outcome.completed_at = datetime.now(timezone.utc)
            outcome.duration_seconds = 0.0
            await self._emit("gladiator_failed", {"gladiator_id": self.gladiator_id, "error": outcome.error})
            return outcome

        reader = ClaudeStreamReader(jsonl_path=stream_jsonl, on_event=self._stream_event_relay)

        async def _drain_stderr() -> None:
            assert proc.stderr is not None
            with stderr_log.open("wb") as fh:
                while True:
                    chunk = await proc.stderr.readline()
                    if not chunk:
                        break
                    fh.write(chunk)
                    fh.flush()

        soft_deadline = time.monotonic() + self.max_gladiator_minutes * 60
        stall_seconds = self.stall_timeout_minutes * 60

        async def _hang_watch() -> str | None:
            while True:
                await asyncio.sleep(min(15, max(2, stall_seconds // 4)))
                if proc.returncode is not None:
                    return None
                now = time.monotonic()
                if now >= soft_deadline:
                    return "soft timeout"
                idle = now - reader.result.last_event_at
                if idle >= stall_seconds:
                    return f"stall ({int(idle)}s with no events)"

        assert proc.stdout is not None
        read_task = asyncio.create_task(reader.read(proc.stdout))
        stderr_task = asyncio.create_task(_drain_stderr())
        hang_task = asyncio.create_task(_hang_watch())

        timeout_reason: str | None = None
        try:
            done, pending = await asyncio.wait(
                {read_task, hang_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if hang_task in done:
                timeout_reason = hang_task.result()
                if timeout_reason:
                    await self._terminate(proc)
                    if not read_task.done():
                        try:
                            await asyncio.wait_for(read_task, timeout=10)
                        except asyncio.TimeoutError:
                            read_task.cancel()
            else:
                hang_task.cancel()

            if not read_task.done():
                try:
                    await read_task
                except Exception as exc:
                    reader.result.error_messages.append(str(exc))

            try:
                await asyncio.wait_for(stderr_task, timeout=5)
            except asyncio.TimeoutError:
                stderr_task.cancel()

            try:
                await asyncio.wait_for(proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                await self._terminate(proc, force=True)
        except Exception as exc:
            outcome.status = QAGladiatorStatus.FAILED
            outcome.error = f"executor loop crashed: {exc}"

        outcome.completed_at = datetime.now(timezone.utc)
        if outcome.started_at:
            outcome.duration_seconds = (outcome.completed_at - outcome.started_at).total_seconds()

        # Persist a plain-text stdout log derived from the stream-json events.
        try:
            stdout_log.write_text(self._render_text_log(reader.result, stream_jsonl), encoding="utf-8")
        except Exception:
            pass

        outcome.token_usage = {
            "input_tokens": reader.result.total_input_tokens,
            "output_tokens": reader.result.total_output_tokens,
            "cache_creation_input_tokens": reader.result.total_cache_creation_tokens,
            "cache_read_input_tokens": reader.result.total_cache_read_tokens,
            "total_tokens": reader.result.total_tokens(),
        }
        outcome.cost_usd = reader.result.total_cost_usd

        if timeout_reason:
            outcome.status = QAGladiatorStatus.TIMED_OUT
            outcome.error = (outcome.error or "") + f" [{timeout_reason}]"
        elif proc.returncode is not None and proc.returncode != 0 and outcome.status != QAGladiatorStatus.FAILED:
            outcome.status = QAGladiatorStatus.FAILED
            tail = self._tail_stderr(stderr_log)
            outcome.error = f"claude exited with {proc.returncode}: {tail}"
        else:
            if outcome.status == QAGladiatorStatus.RUNNING:
                outcome.status = QAGladiatorStatus.COMPLETED

        # Resolve report path with fallback chain.
        resolved_report = self._resolve_report(report_path, reader.result)
        if resolved_report is not None:
            outcome.report_path = str(resolved_report)
            try:
                outcome.raw_report_text = resolved_report.read_text(encoding="utf-8")
            except Exception:
                outcome.raw_report_text = None
            if outcome.status == QAGladiatorStatus.COMPLETED:
                outcome.status = QAGladiatorStatus.REPORT_WRITTEN
        else:
            if outcome.status not in (
                QAGladiatorStatus.FAILED,
                QAGladiatorStatus.TIMED_OUT,
            ):
                outcome.status = QAGladiatorStatus.NO_OUTPUT
            # Last-resort: synthesize a 'report' from the final assistant text
            # so the synthesizer still has something to look at.
            if reader.result.final_result_text:
                outcome.raw_report_text = reader.result.final_result_text
                try:
                    fallback_path = self.gladiator_dir / "report_fallback.md"
                    fallback_path.write_text(
                        f"# Fallback report (gladiator {self.gladiator_id})\n\n"
                        f"No structured report.md was produced. The final assistant message:\n\n"
                        f"{reader.result.final_result_text}\n",
                        encoding="utf-8",
                    )
                    outcome.report_path = str(fallback_path)
                except Exception:
                    pass

        await self._emit(
            "gladiator_finished",
            {
                "gladiator_id": self.gladiator_id,
                "status": outcome.status.value,
                "cost_usd": outcome.cost_usd,
                "tokens": outcome.token_usage.get("total_tokens", 0),
            },
        )
        return outcome

    # ── helpers ─────────────────────────────────────────────────────

    def _build_command(self, prompt: str) -> list[str]:
        cmd = [
            self.claude_binary,
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--permission-mode",
            "bypassPermissions",
            "--no-session-persistence",
            "--session-id",
            self.session_id,
            "--max-budget-usd",
            f"{self.max_budget_usd:g}",
            "--add-dir",
            str(self.gladiator_dir),
            "--setting-sources",
            "project,local",
            "--allowedTools",
            QA_ALLOWED_TOOLS,
        ]
        if self.agent_config.provider.model:
            cmd.extend(["--model", self.agent_config.provider.model])
        if self.fallback_model:
            cmd.extend(["--fallback-model", self.fallback_model])
        cmd.append(prompt)
        return cmd

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self.assigned_gpus:
            env["CUDA_VISIBLE_DEVICES"] = ",".join(str(i) for i in self.assigned_gpus)
        env["QA_OUTPUT_ROOT"] = str(self.gladiator_dir / "scratch")
        env["COLOSSEUM_QA_RUN_ID"] = self.run_id
        env["COLOSSEUM_QA_GLADIATOR_ID"] = self.gladiator_id
        env["COLOSSEUM_QA_REPORT_PATH"] = str(self.gladiator_dir / "report.md")
        env["COLOSSEUM_QA_GOTCHAS_UPDATE"] = "disabled"
        return env

    async def _terminate(self, proc: asyncio.subprocess.Process, force: bool = False) -> None:
        if proc.returncode is not None:
            return
        sig = signal.SIGKILL if force else signal.SIGTERM
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            try:
                proc.send_signal(sig)
            except ProcessLookupError:
                return
        if force:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=30)
        except asyncio.TimeoutError:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass

    def _resolve_report(self, expected: Path, _result: StreamReaderResult) -> Path | None:
        if expected.exists() and expected.is_file() and expected.stat().st_size > 0:
            return expected
        # Fallback: scan gladiator_dir for *.md
        candidates: list[tuple[float, Path]] = []
        for md in self.gladiator_dir.rglob("*.md"):
            try:
                stat = md.stat()
            except OSError:
                continue
            if stat.st_size <= 0:
                continue
            candidates.append((stat.st_mtime, md))
        if not candidates:
            return None
        candidates.sort(reverse=True)
        return candidates[0][1]

    def _tail_stderr(self, path: Path, limit: int = 600) -> str:
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return "(no stderr)"
        if not data:
            return "(empty stderr)"
        return data[-limit:].decode("utf-8", errors="replace")

    def _render_text_log(self, result: StreamReaderResult, stream_jsonl: Path) -> str:
        """Convert stream-json events into a readable plain text log."""
        lines: list[str] = []
        lines.append(f"# Gladiator: {self.gladiator_id} ({self.agent_config.display_name})")
        lines.append(f"# Session ID: {self.session_id}")
        lines.append(f"# Total tokens: {result.total_tokens()}")
        lines.append(f"# Cost: ${result.total_cost_usd:.4f}")
        lines.append("")
        if not stream_jsonl.exists():
            return "\n".join(lines) + "\n"
        try:
            for line in stream_jsonl.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                etype = event.get("type", "")
                if etype == "assistant":
                    msg = event.get("message") or {}
                    for block in msg.get("content") or []:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = str(block.get("text") or "").strip()
                            if text:
                                lines.append(f"[assistant] {text}")
                elif etype == "user":
                    msg = event.get("message") or {}
                    for block in msg.get("content") or []:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            content = block.get("content")
                            if isinstance(content, str):
                                snippet = content[:300]
                                lines.append(f"[tool_result] {snippet}")
                elif etype == "result":
                    res = event.get("result")
                    if isinstance(res, str) and res.strip():
                        lines.append(f"[result] {res.strip()[:600]}")
        except Exception as exc:
            lines.append(f"# (error rendering log: {exc})")
        return "\n".join(lines) + "\n"

    async def _stream_event_relay(self, event: dict[str, Any]) -> None:
        if self.on_event is None:
            return
        etype = event.get("type", "")
        if etype in ("assistant", "user", "result", "system"):
            try:
                await self.on_event(
                    "gladiator_event",
                    {"gladiator_id": self.gladiator_id, "type": etype},
                )
            except Exception:
                pass

    async def _emit(self, name: str, payload: dict[str, Any]) -> None:
        if self.on_event is None:
            return
        try:
            await self.on_event(name, payload)
        except Exception:
            pass
