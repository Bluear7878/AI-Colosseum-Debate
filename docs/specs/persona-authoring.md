# Persona Authoring Specification

## Supported Formats

Colosseum supports both legacy Markdown personas and Markdown personas with optional frontmatter.

### Legacy Markdown

```md
# Pragmatic Engineer

> Ship-focused engineer who balances quality with delivery speed

## Your Role
...
```

### Frontmatter Markdown

```md
---
persona_id: pragmatic_engineer
name: Pragmatic Engineer
description: Ship-focused engineer who balances quality with delivery speed
version: 1.1
tags: [delivery, engineering]
active: true
---
# Pragmatic Engineer

> Ship-focused engineer who balances quality with delivery speed

## Your Role
...
```

## Required Semantics

- The effective persona id must normalize to lowercase snake_case.
- Content must be non-empty.
- The first Markdown heading should match the intended public name.
- The first blockquote should summarize the persona in one sentence.

## Metadata Fields

Supported frontmatter fields:
- `persona_id`
- `name`
- `description`
- `version`
- `tags`
- `active`

Unknown fields are ignored by the current parser and should not be relied on yet.

## Content Guidance

Good persona content should define:
- role and lens
- debate style
- decision priorities
- blind spots or failure modes
- constraints against fabricating user context

Avoid:
- provider-specific instructions
- billing or quota policy
- hidden tool assumptions
- brittle references to a specific run or repository state

## Source Precedence

- `src/colosseum/personas/custom/` overrides `src/colosseum/personas/builtin/` when ids collide.
- Use unique ids unless intentional override behavior is desired.

## Validation Expectations

Before merging a new persona:

1. The file parses through `PersonaRegistry`.
2. The id is stable and sanitized.
3. The content can be loaded through `PersonaLoader`.
4. A test covers any new parser or metadata behavior.

## Authoring Checklist

- Name is clear and human-readable.
- Description fits in one line.
- Prompt body explains stance without overfitting to one task.
- The persona remains useful when attached to different agents.
