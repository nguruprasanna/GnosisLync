# GnosisLync — Svarm Banking Intelligence Platform

**Design specification v0.1 (draft for architecture review)** — 2026-11-20

GnosisLync is Svarm's shared banking intelligence capability:

> **Generic Banking Knowledge + Pluggable Core Banking Product Knowledge + Automatically Discovered Site Knowledge + Specialist Roles and Reusable Skills**, consumed by RIS Sense, EKWORLD ekSense and future Svarm products through one governed API.

## Document map

| # | Document | Covers (master-prompt deliverables) |
|---|---|---|
| 01 | [Architecture Overview](architecture/01-architecture-overview.md) | Principles, architecture diagram, service boundaries, key decisions & trade-offs, exists / reuse / build |
| 02 | [Knowledge Data Model](architecture/02-knowledge-data-model.md) | Entities, relationships, evidence, lifecycle, versioning, packages, DDL |
| 03 | [Discovery Engine & Adapters](architecture/03-discovery-engine-and-adapters.md) | jBASE/T24, SQL, XML/JSON, web app, API, JBC analysis, relationship discovery, cross-source correlation |
| 04 | [Roles, Skills & Tool Registry](architecture/04-roles-skills-registry.md) | Execution classes, role profiles, skill specs (all 27 skills) |
| 05 | [API & Tool Contracts](architecture/05-api-contracts.md) | REST, MCP tool interface, request context, errors |
| 06 | [Ingestion & Enrichment Pipeline](architecture/06-ingestion-enrichment-pipeline.md) | Document/code ingestion, extraction, consolidation, conflicts, gap questions, IP/provenance |
| 07 | [Knowledge Studio](architecture/07-knowledge-studio.md) | Screens, user journeys, review & correction model |
| 08 | [Security, Privacy & Compliance](architecture/08-security-privacy-compliance.md) | AI Gateway, anonymisation, access control, threat model, GDPR/DORA matrix, compliance evidence registry |
| 09 | [Quality Gates & Testing](architecture/09-quality-gates-testing.md) | Unit-test policy, Gherkin repository, EKWORLD execution contract, CI/CD gates, knowledge regression |
| 10 | [Workflows & Worked Examples](architecture/10-workflows-and-examples.md) | New-bank onboarding, NLB examples, RIS & EKWORLD shared-concept examples, end-to-end scenario (§17) |
| 11 | [Implementation Plan](architecture/11-implementation-plan.md) | Repository structure, phased backlog, team responsibilities, failure handling & rollback |

## Reading conventions

- **[ASSUMPTION]** — an assumption about RIS, EKWORLD, NLB or Temenos that must be confirmed by the owning team before build.
- **[VERIFY-T24]** — Temenos product behaviour stated from general product knowledge; it must be confirmed against the release-specific documentation of the target installation before it is promoted to `VERIFIED` knowledge. The platform itself treats such statements the same way.
- **Decision records** are marked `DR-nn` and list alternatives, limitations and trade-offs.

## Non-claims

- These documents describe controls that *support* GDPR and DORA obligations. Neither the documents nor the platform make a bank or Svarm compliant; organisational assessment and independent review are still required.
- Examples involving NLB are illustrative. They use placeholder identifiers and no client data.
