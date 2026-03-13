# Agent Governance Specification

## Scope

This specification defines the contract between runtime agents, personas, provider configuration, and future agent-catalog expansion.

## Terms

- Agent: a runnable participant in a debate. Backed by `AgentConfig`.
- Persona: a reusable reasoning/prompt artifact. Backed by `PersonaDefinition`.
- Provider: execution backend selected by `ProviderConfig`.
- Run: one debate session backed by `ExperimentRun`.

## Current Runtime Schema

### AgentConfig

Required fields:
- `agent_id`
- `display_name`
- `provider`

Optional fields:
- `specialty`
- `system_prompt`
- `persona_id`
- `persona_content`

Rules:
- `agent_id` must be unique within a run.
- `display_name` must be non-empty.
- `persona_id` identifies the selected persona artifact.
- `persona_content` is the frozen prompt content used for the current run. It is intentionally copied into the run boundary so a later persona edit does not silently change history.

### ProviderConfig

Rules:
- `type=command` requires a non-empty command.
- paid providers may carry `quota_key` and `billing_tier`.
- fallback selection is runtime policy, not agent identity.

## Agent vs Persona Boundary

Agents and personas must remain separate concepts.

An agent owns:
- execution backend
- display identity inside one run
- per-run specialty or system prompt
- quota and timeout behavior through its provider

A persona owns:
- reasoning stance
- voice and debate behavior
- reusable prompt content
- authoring metadata such as version and tags

This separation is mandatory because personas will keep growing while agent runtime policy changes on a different cadence.

## Persona Resolution Rules

1. Persona ids are normalized to lowercase snake_case.
2. Custom personas are resolved before builtin personas.
3. Persona content is loaded through the registry, not by directly reading arbitrary files.
4. Optional frontmatter may supply metadata, but the prompt body remains Markdown content.
5. `persona_content` frozen into a run is canonical for that run, even if the source file later changes.

## Versioning Rules

- Persona metadata may include `version`.
- Changing tags or description is a metadata-only change.
- Changing prompt body or core behavioral instructions should bump `version`.
- Existing runs are never back-migrated to newer persona content.

## Deprecation Rules

- Prefer `active: false` over deleting builtins that may still appear in old run artifacts.
- Custom personas may be deleted, but past runs retain their frozen `persona_content`.
- Avoid reusing deleted persona ids for semantically different personas.

## Future Expansion Guidance

When the catalog grows, add fields only if they are operationally meaningful:
- `tags`
- `active`
- `version`
- `owner`
- `safety_notes`

Do not add execution behavior directly to persona files. Provider choice, quotas, and timeout policy belong to agents/providers, not personas.
