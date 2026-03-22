"""Persona management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from colosseum.core.models import (
    ChatPersonaRequest,
    ChatPersonaResult,
    GeneratedPersona,
    PersonaCreateRequest,
    PersonaInterviewRequest,
    PersonaInterviewResult,
    PersonaProfileRequest,
)
from colosseum.personas.generator import PersonaGenerator
from colosseum.personas.loader import PersonaLoader

router = APIRouter()


@router.get("/personas")
async def list_personas():
    loader = PersonaLoader()
    return loader.list_personas()


@router.post("/personas/generate", response_model=GeneratedPersona)
async def generate_persona(request: PersonaProfileRequest) -> GeneratedPersona:
    generator = PersonaGenerator()
    return generator.generate(request)


@router.post("/personas/interview", response_model=PersonaInterviewResult)
async def persona_interview_step(request: PersonaInterviewRequest) -> PersonaInterviewResult:
    """Conduct one step of the persona interview."""
    from colosseum.services.persona_interview import PersonaInterviewService

    service = PersonaInterviewService()
    try:
        return await service.step(request.model, request.messages)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/personas/from-chat", response_model=ChatPersonaResult)
async def generate_personas_from_chat(request: ChatPersonaRequest) -> ChatPersonaResult:
    """Generate debate personas from a chat log."""
    from colosseum.services.chat_persona_generator import ChatPersonaGeneratorService

    service = ChatPersonaGeneratorService()
    try:
        return await service.generate(request.model, request.chat_text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/personas/{persona_id}")
async def get_persona(persona_id: str):
    loader = PersonaLoader()
    content = loader.load_persona(persona_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Persona not found")
    return {"persona_id": persona_id, "content": content}


@router.post("/personas")
async def create_persona(request: PersonaCreateRequest):
    loader = PersonaLoader()
    try:
        return loader.save_custom_persona(request.persona_id, request.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/personas/{persona_id}")
async def delete_persona(persona_id: str):
    loader = PersonaLoader()
    deleted = loader.delete_custom_persona(persona_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Custom persona not found")
    return {"deleted": persona_id}
