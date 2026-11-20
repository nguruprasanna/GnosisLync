# 11 — Implementation Plan

## 1. Repository structure (monorepo)

```
gnosislync/
├── services/
│   ├── gnosis_api/            FastAPI app: routers, request context, policy client, MCP adapter
│   ├── gnosis_worker/         job runner entrypoints (procrastinate)
│   ├── ai_gateway/            separate deployable: pipeline, detectors, transforms, routing, providers, audit
│   └── discovery_runner/      runner entrypoint; loads one adapter per container image variant
├── gnosis/                    core Python package (shared by api/worker)
│   ├── context/  knowledge/  retrieval/  review/  impact/  audit/
│   ├── skills/                registry, runtime, validators; one sub-package per skill
│   ├── roles/                 loader, prompt assembly
│   ├── ingestion/             parsers, segmenters, classifiers, extractors, consolidation, synthesis
│   ├── discovery/             orchestrator, snapshot store, diff
│   ├── adapters/              sql/ (postgres, oracle, mssql, db2), t24_ris/, jbase/, xmljson/, web/, api/
│   ├── analysis/jbc/          lexer, parser, IR, resolver
│   ├── dsl/                   mapping & rule DSL parsers, SQL generation (sqlglot)
│   └── integration/           ris/, ekworld/ clients
├── packs/
│   ├── banking_core/          taxonomy, seed concepts, calculation reference implementations, coverage model
│   └── temenos_transact/      release catalogue, entity kinds, record-format decoders, application dictionary, extraction profiles
├── roles/                     *.yaml role profiles
├── prompts/                   versioned prompt templates (*.md.j2)
├── skills/                    skill manifests + I/O JSON schemas
├── policies/                  llm/, opa/ (rego), discovery/
├── db/migrations/             Alembic
├── studio/                    Knowledge Studio (React + TS)
├── tests/
│   ├── unit/ db/ contract/ security/ knowledge/
│   ├── gherkin/               (doc 09 §4.1)
│   └── fixtures/synthetic/    synthetic banks, schemas, JBC corpus (sanitised)
├── evals/                     per-skill evaluation sets
├── deploy/                    helm charts, network policies, Vault policies
├── docs/                      this specification, ADRs
└── testmap.yaml  .pre-commit-config.yaml  .github/workflows/  CODEOWNERS
```

`import-linter` contracts: `gnosis.adapters` cannot import `gnosis.knowledge` (adapters only produce snapshots). Only `gnosis.integration.llm` may call the AI Gateway. `gnosis.review` is the only writer of `entity_head.verified_version`.

## 2. Phased delivery

Phases are sized for a core team of about 6–8 engineers plus domain specialists. Durations are indicative and depend on Phase 0 findings.

### Phase 0 — Foundations & verification (≈ 4 weeks)
| Item | Type | Exit criterion |
|---|---|---|
| Confirm RIS T24 metadata interface (API shape, auth, release info, local refs, MV/SV flags) | Reuse — verify | Contract doc + recorded fixtures |
| Confirm RIS export lineage knowledge (how EXP tables map to T24 fields) | Reuse — verify | Sample lineage for 5 applications |
| Confirm EKWORLD discovery output API and suite execution API | Reuse — verify | Agreed contract (doc 09 §4.3) |
| Legal: documentation licences (Temenos via Svarm / via NLB), cross-client reuse rules | Decision | `licence_policy` entries signed off |
| NLB: approval for read-only access to export DB (UAT), DPA/purpose, LLM policy (external allowed?) | Decision | Approved source + tenant LLM policy |
| Repo skeleton, CI gates (doc 09 §3), Postgres/pgvector, Vault, IdP integration | Build | Green pipeline with branch protection |
| Data model migrations v1 (doc 02), RLS + isolation tests | Build | Isolation suite passes |

### Phase 1 — Knowledge core & product ingestion (≈ 8 weeks) — scope items 1–4
| Item | Type |
|---|---|
| Knowledge registry, lifecycle state machine, change sets, review decisions, packages | Build |
| AI Gateway v1 (detect/redact/pseudonymise, routing, audit, fail-closed, inspection stub) | Build |
| Ingestion pipeline stages 0–8, taxonomy seed (banking_core), Temenos pack skeleton with release catalogue | Build |
| Ingest generic banking corpus (Svarm-authored + licensed), Temenos Transact functional docs, JBC/TAFC/TAFJ technical docs | Content |
| Hybrid retrieval with scope/release/tenant filtering; `get_banking_concept`, `get_product_knowledge`, `get_site_context` | Build |
| Site model (tenant/site/instance/environment/sources) API | Build |
| Studio v1: Acquire (upload, jobs), Explore (browse, search, entity page), Review (change sets) | Build |

### Phase 2 — NLB site discovery & JBC analysis (≈ 8 weeks) — scope items 5–7
| Item | Type |
|---|---|
| `t24.ris` adapter → `discover_applications`, `discover_fields` | Reuse (RIS) + build wrapper |
| `scan_sql_database` (Oracle first, then Postgres/MSSQL), `discover_schema`, snapshot diff, `compare_schema_versions` | Build |
| `analyze_xml_json_schema` + TAFJ record-format decoder (if export contains XML records) | Build |
| JBC lexer/parser/IR + resolver against site fields; `analyze_legacy_code` (deterministic + LLM interpretation) | Build |
| Discovery runners (sql, t24) with Vault leases, preflight read-only checks, checkpoint/resume | Build |
| Studio: Sources, job monitor, IR/code evidence viewer | Build |

### Phase 3 — Relationships, mapping, review & publication (≈ 8 weeks) — scope items 8–10
| Item | Type |
|---|---|
| `discover_relationships` (channels 1–5; profiling opt-in), `correlate_knowledge`, `detect_knowledge_conflicts` | Build |
| `extract_business_rules`, rule DSL; `resolve_source_fields`, `generate_data_mapping`, `generate_sql`, `validate_mapping` | Build |
| Knowledge regression engine, golden assertions, package publication, webhooks, consumer-artefact registration, impact analysis | Build |
| Public `/v1` API + MCP adapter; Pact contracts with RIS Sense and EKWORLD | Build |
| Studio: relationship viewer, conflicts, open questions, contributions, packages, quality dashboard v1 | Build |
| RIS Sense integration (NLB pilot: 1 regulatory report end-to-end) | Integrate |
| EKWORLD integration: `rules:search`, `test-scenarios:generate`, regression suite execution for platform tests | Integrate |

### Phase 4 — Application & API discovery, hardening (≈ 6–8 weeks)
`discover_web_application` (reusing EKWORLD discovery), `discover_api` (OpenAPI/Postman), cross-source correlation incl. UI/API, `scan_jbase_environment` from offline exports, DSR workflow, compliance evidence registry automation, DR drills, pen test.

### Phase 5 — Extension (later)
Java/TAFJ customisation analysis, live jBASE connectivity (if approved), Oracle FLEXCUBE pack + PL/SQL analysis, graph DB projection if traversal needs exceed Postgres, additional consumers (migration, reconciliation).

**Out of initial scope (per §12):** direct extraction from live T24 databases, FLEXCUBE adapters, autonomous production changes, large agent hierarchies.

## 3. Backlog view: exists / reuse / build

| Epic | Exists | Reuse | Build |
|---|---|---|---|
| T24 metadata discovery | RIS T24 app/field retrieval | Wrap via adapter | Normalisation, release matching, diffing |
| Export data discovery | NLB export DB (RIS) | Read-only access | SQL adapters, schema discovery, lineage |
| Report execution | RIS Core | Unchanged | — |
| UI discovery | EKWORLD Playwright discovery | Call/consume output | Banking enrichment, guards for GnosisLync runner |
| Test execution | EKWORLD Playwright/Robot | Execute Gherkin suites | Step definitions for gateway/knowledge checks |
| Knowledge platform | — | — | Model, pipeline, skills, gateway, Studio, APIs |
| Identity, secrets, observability | Svarm IdP/Vault/monitoring [ASSUMPTION] | Integrate | Policies, dashboards |

## 4. Team responsibilities

| Team / role | Owns |
|---|---|
| **Platform core** (2–3 eng) | Knowledge registry, lifecycle, review/publication, retrieval, API/MCP, impact & regression |
| **Discovery & analysis** (2 eng, one with T24/jBC background) | Adapters, runners, JBC parser/IR, XML/JSON inference, snapshot diff |
| **Intelligence** (1–2 eng) | Skill runtime, prompts, role profiles, extraction/correlation, evaluations |
| **Security & privacy** (1 eng + Privacy Officer, part-time) | AI Gateway, policies, threat model, compliance registry, DSR |
| **Studio** (1–2 FE eng + UX) | Knowledge Studio |
| **Domain specialists** (Svarm: GL, Lending/AA, Payments, Temenos functional, T24 technical, data) | Taxonomy, seed knowledge, review, golden assertions, eval labelling. **Capacity planning:** ~0.5 FTE review per active site during baseline |
| **RIS team** | RIS metadata interface contract, RIS Sense integration, RIS Core submission |
| **EKWORLD team** | Discovery API, execution API, step library, report scrubbing |
| **Knowledge Steward** | Taxonomy governance, merges, package publication |

## 5. Key risks

| Risk | Impact | Mitigation |
|---|---|---|
| Reviewer capacity becomes the bottleneck | Knowledge stays CANDIDATE | Demand-driven prioritisation, batching of non-financial items, provisional use, deterministic auto-verification for metadata facts |
| Licence terms limit reuse of Temenos docs | Product pack must be per-tenant | Phase 0 legal review. Svarm-authored knowledge as the base |
| RIS interface lacks MV/SV or release info | Weaker field model | Tri-state attributes. Supplement from export structure and docs |
| No external LLM allowed for some banks | Quality/cost of private models | Private model path designed from the start. Evaluations run per provider |
| JBC diversity (legacy styles, dynamic `EXECUTE`) | Unresolved references | Explicit `unresolved` list, specialist questions, incremental grammar |
| Over-trust in AI output by consumers | Wrong reports/tests | VERIFIED-only default, status labels, validators, four-eyes on financial items |

## 6. Failure handling, rollback and recovery

| Failure | Detection | Handling | Recovery |
|---|---|---|---|
| Discovery runner crash / network loss | Job heartbeat timeout | Run → `CHECKPOINTED`. Lease released | `:resume` continues from `discovery_run.checkpoint`. Batches are idempotent (upsert by canonical key + snapshot id). Snapshot finalised only when complete |
| Source unavailable / privileges revoked | Preflight / adapter error | `FAILED` with code. No partial snapshot promoted | Retry policy. Source owner notified |
| Ingestion stage failure | Step exception | Stage marked failed. Other artefacts continue | Re-run stage (idempotent on `(sha256, stage, version)`) |
| LLM provider outage | Gateway circuit breaker | Fallback per policy or `LLM_UNAVAILABLE`. Deterministic features unaffected | Auto-close circuit. Queued LLM steps retry with backoff |
| Bad knowledge published | Consumer report / regression | **Withdraw** package version (`WITHDRAWN`). Consumers' pin auto-falls back to the previous published version, and webhook informs them | Correct via change set → new version |
| Faulty application release | SLO/canary alerts | Automatic rollback (blue/green) | Expand/contract migrations ensure previous version compatible |
| DB corruption / loss | Monitoring | Failover to replica | PITR (RPO ≤ 5 min), RTO ≤ 4 h (targets to be agreed with banks) |
| Audit chain break | Nightly verification | Security incident raised | Forensics. Restore from WORM evidence |
| Gateway policy misconfiguration | Policy validation at deploy (unit tests + `opa test`) | Deploy blocked | — |

Knowledge rollback is version-based and loses nothing: package versions are immutable, so "rollback" re-points consumers to a prior published version. Entity heads are restored by publishing a change set that re-approves prior versions, with an audited decision and no destructive updates.

## 7. First 30 days (concrete start)

1. Repo skeleton per §1 with CI gates, CODEOWNERS and branch protection.
2. Alembic migration `0001` with topology, entity/relation/version, evidence, change-set and package tables plus RLS, and the isolation test suite.
3. AI Gateway skeleton: Presidio + IBAN/PAN/secret recognisers, fail-closed path, inspection stub, plus the privacy Gherkin feature (doc 09 §5) running in EKWORLD against staging.
4. `t24.ris` adapter against recorded RIS fixtures, and `discover_fields` producing OBSERVED site fields.
5. Oracle `scan_sql_database` preflight + metadata scan against a synthetic Oracle schema shaped like the NLB export (no client data).
6. Studio shell: login, context bar, upload, job monitor, entity page (read-only).
