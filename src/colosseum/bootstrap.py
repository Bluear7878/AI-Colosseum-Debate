from __future__ import annotations

from functools import lru_cache

from colosseum.services.budget import BudgetManager
from colosseum.services.context_bundle import ContextBundleService
from colosseum.services.debate import DebateEngine
from colosseum.services.judge import JudgeService
from colosseum.services.local_runtime import LocalRuntimeService
from colosseum.services.normalizers import ResponseNormalizer
from colosseum.services.orchestrator import ColosseumOrchestrator
from colosseum.services.provider_runtime import ProviderRuntimeService
from colosseum.services.qa_finding_clusterer import QAFindingClusterer
from colosseum.services.qa_gpu_allocator import QAGpuAllocator
from colosseum.services.qa_orchestrator import QAOrchestrator
from colosseum.services.qa_report_parser import QAReportParser
from colosseum.services.qa_report_synthesizer import QAReportSynthesizer
from colosseum.services.qa_repository import QARunRepository
from colosseum.services.report_synthesizer import ReportSynthesizer
from colosseum.services.repository import FileRunRepository
from colosseum.services.review_orchestrator import ReviewOrchestrator


@lru_cache(maxsize=1)
def get_orchestrator() -> ColosseumOrchestrator:
    budget_manager = BudgetManager()
    normalizer = ResponseNormalizer()
    repository = FileRunRepository()
    context_service = ContextBundleService()
    provider_runtime = ProviderRuntimeService(budget_manager=budget_manager)
    judge_service = JudgeService(
        budget_manager=budget_manager,
        provider_runtime=provider_runtime,
    )
    debate_engine = DebateEngine(
        budget_manager=budget_manager,
        normalizer=normalizer,
        provider_runtime=provider_runtime,
    )
    report_synthesizer = ReportSynthesizer(provider_runtime=provider_runtime)
    return ColosseumOrchestrator(
        repository=repository,
        context_service=context_service,
        debate_engine=debate_engine,
        judge_service=judge_service,
        budget_manager=budget_manager,
        normalizer=normalizer,
        provider_runtime=provider_runtime,
        report_synthesizer=report_synthesizer,
    )


@lru_cache(maxsize=1)
def get_review_orchestrator() -> ReviewOrchestrator:
    return ReviewOrchestrator(orchestrator=get_orchestrator())


@lru_cache(maxsize=1)
def get_qa_orchestrator() -> QAOrchestrator:
    """Build the QA ensemble orchestrator and its dependencies."""
    local_runtime = LocalRuntimeService()
    gpu_allocator = QAGpuAllocator(local_runtime=local_runtime)
    repository = QARunRepository()
    parser = QAReportParser()
    # Reuse the debate orchestrator's provider runtime so that quota tracking,
    # paid-tier policies, and quota recovery all work for the QA judge call too.
    base_orchestrator = get_orchestrator()
    provider_runtime = base_orchestrator.provider_runtime
    synthesizer = QAReportSynthesizer(provider_runtime=provider_runtime)

    def _clusterer_factory(target_root: str) -> QAFindingClusterer:
        return QAFindingClusterer(target_root=target_root)

    return QAOrchestrator(
        gpu_allocator=gpu_allocator,
        repository=repository,
        report_parser=parser,
        clusterer_factory=_clusterer_factory,
        synthesizer=synthesizer,
        provider_runtime=provider_runtime,
        local_runtime=local_runtime,
    )
