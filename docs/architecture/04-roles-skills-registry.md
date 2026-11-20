# 04 — Roles, Skills and Tool Registry

## 1. Execution classes — what is (and is not) an agent

| Class | Definition | Examples | Determinism / audit |
|---|---|---|---|
| **Deterministic service** | Plain code, no LLM, no skill contract needed | Release resolution, RLS, snapshot diff, lifecycle state machine, package publication | Unit-tested, fully reproducible |
| **Deterministic skill** | Registered, versioned operation with typed I/O, no LLM | `scan_sql_database`, `discover_schema`, `compare_schema_versions`, `validate_mapping` | Reproducible given same snapshot |
| **LLM-assisted skill** | Registered operation using a **single or fixed sequence of** LLM calls with schema-constrained output and deterministic validators | `extract_domain_knowledge`, `generate_data_mapping`, `explain_decision` | Output stored with prompt hash, model id and input hashes. Replayable for audit (not bit-identical) |
| **Bounded reasoning loop** | LLM selects from an allow-listed set of *read-only* skills for up to N steps | `resolve_source_fields` (N≤8), `correlate_knowledge` adjudication for complex clusters (N≤5) | Full step trace persisted. No write skills in the allow-list |
| **Role** | A configuration profile (not a process) | "Lending/AA Specialist" | Versioned config |
| **Human** | Specialist review and contribution | Knowledge Studio | Signed decisions |

There are no autonomous multi-agent hierarchies. Writes to knowledge happen only through the change-set mechanism.

## 2. Role profiles

A role is a profile applied when a skill runs on behalf of a request. It does four things:
1. selects **prompt framing** (expertise, vocabulary, cautions),
2. restricts **knowledge access** (domains, scopes),
3. restricts **allowed skills**,
4. defines **review authority** for humans holding the matching review role.

```yaml
# roles/lending_aa_specialist.yaml
id: lending_aa_specialist
version: 1.2.0
display_name: Lending / AA Specialist
domains: [LENDING, INTEREST_FEES, ACCOUNTING]            # taxonomy codes
products: [TEMENOS_TRANSACT]
knowledge_access:
  scopes: [GENERIC, PRODUCT, SITE, SVARM_INTERNAL]
  entity_kinds_deny: []                                   # e.g. HR-only kinds
prompt:
  system_template: prompts/roles/lending_aa_specialist.md.j2   # versioned, hash recorded
  cautions:
    - "Distinguish current, due and overdue principal; never sum interest into principal."
    - "AA behaviour is product-condition driven; site configuration overrides product defaults."
allowed_skills:
  - get_banking_concept
  - get_product_knowledge
  - get_site_context
  - resolve_source_fields
  - extract_business_rules
  - generate_data_mapping
  - validate_mapping
  - generate_test_scenarios
  - explain_decision
review_authority:               # applies to HUMAN principals holding this role
  can_approve:
    domains: [LENDING, INTEREST_FEES]
    max_financial_significance: HIGH
    entity_kinds: [banking_concept, business_rule, calculation, site_rule, mapping, realised_by]
  requires_second_approver_when: {financial_significance: HIGH, op: [UPDATE, MARK_INCORRECT]}
```

| Role | Domains | Typical skills | Review authority |
|---|---|---|---|
| Banking GL Specialist | GL, ACCOUNTING, RECON, REGULATORY | concept, rules, validate_mapping, explain | GL concepts/rules, accounting mappings (HIGH) |
| Lending/AA Specialist | LENDING, INTEREST_FEES | as above | Lending concepts, AA product/site knowledge (HIGH) |
| Payments Specialist | PAYMENTS, FX, SETTLEMENT | as above | Payments (HIGH) |
| Temenos Functional Specialist | all Transact functional | get_product_knowledge, correlate, extract_domain_knowledge | Product-pack functional knowledge, site functional config |
| T24 Technical Developer | RUNTIME (TAFC/TAFJ/jBC) | analyze_legacy_code, scan_jbase_environment, discover_* | Code facts, runtime topics, technical relations (≤ MEDIUM) |
| Core Banking Data Specialist | DATA | discover_schema, discover_relationships, analyze_xml_json_schema | Schema relations, export lineage (≤ MEDIUM; HIGH with co-approval) |
| Data/Reporting Engineer | REPORTING | generate_sql, generate_data_mapping, validate_mapping | Mappings ≤ MEDIUM; SQL correctness |
| Knowledge Steward (platform) | all | review_knowledge_change, detect_knowledge_conflicts | Merges, taxonomy, deprecation; **not** financial semantics |
| Privacy Officer (platform) | — | — | Source approvals with PII, policy exceptions |

Role selection: callers may *request* a role. The policy engine intersects it with the principal's entitlements. For system-initiated jobs the role is set by the job definition.

## 3. Skill registry specification

Every skill is registered with a manifest. The runtime refuses to execute a skill whose manifest is invalid, unversioned or lacks tests (CI gate, doc 09).

```yaml
# skills/resolve_source_fields/skill.yaml
id: resolve_source_fields
version: 0.4.0
summary: Resolve a banking concept or report requirement to site-specific source fields.
execution_class: BOUNDED_LOOP           # DETERMINISTIC | LLM_ASSISTED | BOUNDED_LOOP
max_steps: 8
tool_allowlist: [get_banking_concept, get_product_knowledge, get_site_context, discover_relationships.read]
input_schema: schemas/input.json
output_schema: schemas/output.json
permissions:
  required: [knowledge:read:site]
  data_class_max: METADATA_ONLY         # skill never needs row data
knowledge_dependencies: [GENERIC, PRODUCT, SITE]
llm:
  purpose_tag: FIELD_RESOLUTION          # used by AI Gateway routing policy
  prompt: prompts/resolve_source_fields.v4.md.j2
  temperature: 0
  response_format: json_schema
validators:                              # deterministic, run on every output
  - all_fields_exist_in_site_package
  - release_compatible
  - cited_evidence_ids_exist_and_authorised
  - no_candidate_presented_as_verified
ambiguity_policy:
  multiple_candidates: return_ranked_with_disambiguation_questions
  no_candidate: return_gap_and_create_open_question
side_effects: none                       # read-only skill
emits_audit: [SKILL_INVOKED, LLM_CALL_REF]
owners: [team-intelligence]
tests: tests/skills/resolve_source_fields/
```

Common output envelope (all skills):

```json
{
  "result": { "...skill-specific..." },
  "status": "OK | AMBIGUOUS | INSUFFICIENT_KNOWLEDGE | BLOCKED_BY_POLICY | FAILED",
  "knowledge_state": "VERIFIED_ONLY | INCLUDES_PROVISIONAL | INCLUDES_CANDIDATES",
  "evidence": [{"evidence_id":"…","source_title":"…","locator":{…},"basis":"SOURCE_METADATA","disclosure":"SYNTHESIS_ONLY"}],
  "assumptions": ["…"],
  "questions": [{"id":"…","text":"…","suggested_role":"lending_aa_specialist"}],
  "package_versions": {"banking-core":"3.2.0","temenos-transact":"1.4.1","NLB/T24-PRIMARY/UAT":"0.7.0"},
  "trace_id": "…", "skill": "resolve_source_fields@0.4.0"
}
```

## 4. Skill catalogue

The §5 and §16 lists are merged into one registry with no product-specific duplicates. `scan_*` skills read sources and produce snapshots. `discover_*` skills normalise snapshots into knowledge. The §16 discovery names are the scan layer; the §5 `discover_*` names are the normalisation layer.

Legend: **Class** D = deterministic, L = LLM-assisted, B = bounded loop. **Perm** = required permission scopes. **Status out** = highest lifecycle status the skill can create.

### 4.1 Knowledge access

| Skill | Class | Inputs → Outputs | Perm | Knowledge deps | Deterministic validation | Ambiguity / errors | Evidence returned |
|---|---|---|---|---|---|---|---|
| `get_banking_concept` | D (+ optional L for natural-language query rewrite) | `{query \| concept_key, include:[rules,calcs,relations]}` → concept(s) with definitions, variants, rules | `knowledge:read:generic` | GENERIC | Only published versions unless `include_candidates` and caller allowed | Multiple matches → ranked list + disambiguation hints | Evidence refs per version (disclosure-filtered) |
| `get_product_knowledge` | D | `{product, release, key \| query, kinds}` → product entities applicable to release | `knowledge:read:product` + licence check | PRODUCT | Release range filter. `UNSTATED` warning | Release unknown → `AMBIGUOUS`, requires release | Doc evidence, licence-filtered |
| `get_site_context` | D | `{environment}` → site profile: release, runtime, DB, modules, package version, key config | `knowledge:read:site` | SITE | Env must belong to caller tenant (RLS) | Env history ambiguity → explicit `as_of` | Discovery run refs |

### 4.2 Scan layer (source access; run in discovery runners)

| Skill | Class | Inputs → Outputs | Perm | Deterministic validation | Ambiguity / errors | Evidence |
|---|---|---|---|---|---|---|
| `scan_sql_database` | D | `{source_id, schemas_allow, options}` → snapshot | `discovery:run:sql` + approved source | Preflight read-only check. Object counts reconcile with catalogue | Partial privileges → `PARTIAL` with list of unreadable objects | Snapshot sha256, catalogue query ids |
| `scan_jbase_environment` | D | `{source_id \| export_artifacts}` → snapshot (files, dicts, code catalogue) | `discovery:run:jbase` | Command allow-list. Dictionary parse validation | Unknown dict conversions → kept raw, flagged | Snapshot + export artefact ids |
| `discover_web_application` | D crawl + L enrichment | `{source_id(url), account_ref, budget}` → UI snapshot + candidate concept links | `discovery:run:web` + env approval | Action guard, network guard, crawl budget | Login/MFA failure → `BLOCKED`. Dynamic content → marks `INCOMPLETE_PAGE` | DOM paths, masked screenshots (optional), HAR excerpt refs |
| `discover_api` | D parse + L grouping | `{spec_artifact \| url, endpoints_allow, validate:bool}` → API snapshot + candidates | `discovery:run:api` | Spec validation (openapi-spec-validator). Mutation block | Spec/impl mismatch → conflict item | Spec locators (`#/paths/...`), safe-call response shapes |
| `ingest_banking_document` | D parse + L classify | `{artifact_id, declared_scope, product, release?}` → sections, classification, chunks | `knowledge:ingest` + licence | Parser QA (text yield, OCR confidence). Licence present | Unclassifiable → `NEEDS_TRIAGE` queue | Page/section locators |

### 4.3 Normalisation & analysis

| Skill | Class | Inputs → Outputs | Perm | Deterministic validation | Ambiguity / errors | Status out |
|---|---|---|---|---|---|---|
| `discover_applications` | D | `{environment}` via `t24.ris` → `site_application` | `discovery:run:t24` | Count reconciliation vs RIS | RIS unavailable → retry/backoff, `FAILED` with resumable checkpoint | OBSERVED |
| `discover_fields` | D | `{environment, applications?}` → `site_field` + `instance_of` | `discovery:run:t24` | Field-no uniqueness per app. MV/SV tri-state | No definition text generated (by design) | OBSERVED |
| `discover_schema` | D | `{snapshot_id}` → db entities + diff vs previous | `discovery:run:sql` | Referential integrity of snapshot | Undeclared PKs → flagged | OBSERVED |
| `analyze_xml_json_schema` | D (statistical) | `{schema_artifact \| sample_ref, record_format?}` → `structured_path` entities | `discovery:sample` if samples | Occurrence stats. Type-set consistency | Variable structure → `VARIABLE_STRUCTURE` + uncertainty | OBSERVED (structure) / CANDIDATE (semantics) |
| `discover_relationships` | D channels + L explanation | `{environment, scope_filter, channels[]}` → relation candidates | `knowledge:propose` (+ `discovery:profile` for channel 6) | Basis ceiling rules (doc 03 §7) | Conflicting channels → both kept, conflict raised | VERIFIED only for metadata-declared, else CANDIDATE |
| `analyze_legacy_code` | D parse + L interpret | `{artifact_id \| code_unit, environment}` → IR + code facts + interpretation | `knowledge:propose`, `code:read` | IR citations. Field refs resolved vs site package | Unresolved refs listed. Parse errors → partial IR + error spans | OBSERVED (facts) / CANDIDATE (meaning) |
| `extract_business_rules` | L | `{ir \| doc_section_ids, domain}` → `business_rule`/`site_rule` candidates (condition, action, scope) | `knowledge:propose` | Every rule cites evidence. Rule expressed in the rule DSL (§5) must parse | Contradiction with existing → conflict item | CANDIDATE |
| `extract_domain_knowledge` | L | `{section_ids, taxonomy_hint}` → concepts, rules, calcs, relations, terms | `knowledge:propose` | Schema-valid. Overlap filter (doc 06 §7). Citation check | Low-yield section → skipped with reason | CANDIDATE |
| `correlate_knowledge` | D blocking + B adjudication | `{concept_keys \| environment}` → cross-source link candidates | `knowledge:propose` | Citation & type-compatibility checks | Ties → ranked, question generated | CANDIDATE |
| `detect_knowledge_conflicts` | D + L | `{entity_versions \| change_set}` → conflicts (`contradicts`) with explanation | `knowledge:read` | Deterministic: attribute contradictions (types, ranges, expressions). L: textual contradictions | Unsure → `POSSIBLE_CONFLICT` | conflict records |
| `compare_schema_versions` | D | `{snapshot_a, snapshot_b}` or `{env_a, env_b}` → structural diff + materiality | `knowledge:read:site` | — | Rename heuristic flagged as candidate | diff record |

### 4.4 Generation & validation

| Skill | Class | Inputs → Outputs | Perm | Deterministic validation | Ambiguity / errors |
|---|---|---|---|---|---|
| `resolve_source_fields` | B | `{concept \| requirement_text, environment, target_grain}` → ranked field sets with join paths | `knowledge:read:site` | Fields exist in published package. Join path exists in relation graph | Multiple → disambiguation questions. None → gap + `open_question` |
| `generate_data_mapping` | L | `{target_spec, resolved_fields, rules}` → mapping definition (DSL §5) | `mapping:propose` | DSL parses. Types compatible. Every source field resolved. Financial items flagged | Unmappable target → explicit `UNMAPPED` with reason |
| `generate_sql` | L + D | `{mapping_id, dialect, environment}` → SQL + explain plan check | `mapping:propose` | `sqlglot` parse/transpile. Only SELECT. Referenced objects exist in snapshot. `EXPLAIN` on approved non-prod source optional | Dialect gap → manual-review flag |
| `validate_mapping` | D | `{mapping_id \| mapping_doc, environment, recon_spec?}` → validation report | `mapping:validate` | Static checks (types, nullability, MV/SV explode, currency handling, date/business-date filters), recon totals vs GL (if recon source authorised, aggregate-only) | Each failed check → severity |
| `generate_test_scenarios` | L | `{rule_ids \| concept, environment, categories[]}` → Gherkin scenarios + data requirements + trace links | `test:propose` | Gherkin parse (`gherkin-official`). Every scenario tags a rule/requirement. Steps match the EKWORLD step library or are flagged `UNBOUND_STEP` | Rules lacking expected values → scenario marked `NEEDS_EXPECTED_RESULT` |
| `explain_decision` | L | `{trace_id \| entity_version \| mapping_id, audience}` → explanation with evidence | `knowledge:read` | Every statement cites evidence. Disclosure policy filter | Missing evidence → states it explicitly |
| `enrich_site_knowledge` | D orchestration of the above | `{environment, plan}` → job graph → change sets | `knowledge:propose`, `discovery:*` as needed | Plan validated against policy | Step failure → checkpoint, continue independent branches |
| `review_knowledge_change` | D (+ L summary) | `{change_set_id, decisions[]}` → state transitions, new package candidates | `knowledge:review:<domain>` | Lifecycle invariants (doc 02 §4). Reviewer authority. Four-eyes for HIGH | Concurrent modification → `409 BASE_VERSION_CHANGED` |

## 5. Mapping and rule DSLs (deterministic validation targets)

LLM skills produce structures in small DSLs, not free text. This makes validation and SQL generation deterministic.

```yaml
# mapping DSL (subset)
mapping: NLB_LOAN_PRINCIPAL_OS
target: {report: CRR_LOANS, field: PRINCIPAL_OUTSTANDING, type: DECIMAL(19,2), currency_from: CCY}
grain: {entity: arrangement, key: EXP_AA_ARRANGEMENT.ARRANGEMENT_ID, as_of: BUSINESS_DATE}
sources:
  - alias: arr
    object: site:sql:T24EXP.EXP_AA_ARRANGEMENT
    filter: "arr.PRODUCT_LINE = 'LENDING' AND arr.ARR_STATUS IN ('CURRENT','EXPIRED')"
  - alias: bal
    object: site:sql:T24EXP.EXP_ECB_BALANCE_TYPE    # exploded MV of EB.CONTRACT.BALANCES
    join: {to: arr, on: "bal.CONTRACT_ID = arr.ARRANGEMENT_ID", cardinality: "1:N"}
derivation:
  expr: "SUM(bal.OPEN_BALANCE + bal.CREDIT_MVMT + bal.DEBIT_MVMT)"
  where: "bal.BALANCE_TYPE IN @site.principal_balance_types"   # site_rule reference, not literal
  group_by: [arr.ARRANGEMENT_ID, arr.CURRENCY]
concepts: [concept:lending.outstanding_principal]
financial_significance: HIGH
evidence: [ev-…, ev-…]
```

Rule DSL: `WHEN <condition over concept attributes> THEN <effect> [UNLESS <exception>] SCOPE <product/site>` is parsed to an AST and stored in `rule_detail.ast`. The same AST drives test-scenario generation and conflict detection.

## 6. MCP / tool exposure

Every read-only skill and every `*.propose` skill is exposed as a tool via the MCP adapter (doc 05 §4). Write-side operations (`review_knowledge_change` decisions, publication) are **not** exposed to AI clients. They require a human session in Knowledge Studio.
