"""Provider quota management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from colosseum.bootstrap import get_orchestrator
from colosseum.core.models import ProviderQuotaBatchUpdate, ProviderQuotaState

router = APIRouter()


@router.get("/provider-quotas", response_model=list[ProviderQuotaState])
async def list_provider_quotas(
    orchestrator=Depends(get_orchestrator),
) -> list[ProviderQuotaState]:
    return orchestrator.provider_runtime.list_quota_states()


@router.put("/provider-quotas", response_model=list[ProviderQuotaState])
async def update_provider_quotas(
    request: ProviderQuotaBatchUpdate,
    orchestrator=Depends(get_orchestrator),
) -> list[ProviderQuotaState]:
    return orchestrator.provider_runtime.upsert_quota_states(request.states)
