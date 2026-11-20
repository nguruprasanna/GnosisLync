# 05 — Intelligence API and Tool-Calling Contracts

## 1. Conventions

- Base path `/v1`. JSON. OpenAPI 3.1 is generated from FastAPI/Pydantic and published as the contract artefact. Contract tests run in CI (doc 09).
- AuthN: OIDC bearer tokens. Service-to-service calls (RIS Sense, EKWORLD) use client-credentials with mTLS inside the cluster.
- **Request context** travels in headers. The server derives the *effective* context from the token plus the policy decision, and **never trusts the header alone**:

| Header | Meaning | Enforcement |
|---|---|---|
| `X-Gnosis-Tenant` | Tenant code | Must be in token `tenants` claim (or service entitlement table). Sets `gnosis.tenant_ids` for RLS |
| `X-Gnosis-Environment` | `NLB/T24-PRIMARY/UAT` | Must belong to tenant. Resolves release/runtime server-side |
| `X-Gnosis-Product` / `X-Gnosis-Release` | Optional assertion | If supplied and ≠ environment's release → `409 RELEASE_MISMATCH` (prevents "wrong release selected") |
| `X-Gnosis-Role` | Requested role profile | Intersected with entitlements |
| `X-Gnosis-Package` | Pin a published package version | Default: latest published for env |
| `X-Gnosis-Knowledge-State` | `VERIFIED_ONLY` (default) \| `INCLUDE_PROVISIONAL` \| `INCLUDE_CANDIDATES` | Candidates require `knowledge:read:candidates` |
| `X-Gnosis-Purpose` | `REPORTING` \| `TESTING` \| `KNOWLEDGE_ADMIN` … | Purpose limitation (GDPR Art. 5(1)(b)) recorded in audit and used by gateway routing |
| `traceparent` | W3C trace | Propagated |

- Errors use RFC 9457 problem details: `type`, `title`, `status`, `detail`, plus `gnosis_code` (e.g. `RELEASE_MISMATCH`, `TENANT_FORBIDDEN`, `BLOCKED_BY_PRIVACY_POLICY`, `BASE_VERSION_CHANGED`, `LLM_UNAVAILABLE`, `KNOWLEDGE_INSUFFICIENT`).
- Long-running operations return `202` + `Location: /v1/jobs/{id}`.
- Idempotency: `Idempotency-Key` header on all POSTs that create jobs or change sets.

## 2. Resource map

| Area | Endpoint | Purpose |
|---|---|---|
| Topology | `POST/GET /v1/tenants`, `/v1/tenants/{t}/sites`, `/v1/sites/{s}/instances`, `/v1/instances/{i}/environments` | Admin: site, instance, environment (release, runtime, DB) |
| Sources | `POST /v1/environments/{e}/sources`, `POST /v1/sources/{id}:approve`, `POST /v1/sources/{id}:preflight` | Register DB/URL/API/jBASE sources; approval; read-only preflight |
| Artefacts | `POST /v1/artifacts` (multipart, with scope/product/release/licence) | Upload documents, code, specs |
| Jobs | `POST /v1/discovery-runs`, `POST /v1/ingestion-jobs`, `GET /v1/jobs/{id}`, `POST /v1/jobs/{id}:cancel`, `:resume` | Start/monitor discovery and ingestion |
| Knowledge | `GET /v1/entities?…`, `GET /v1/entities/{key}`, `GET /v1/entities/{key}/versions`, `GET /v1/entities/{key}/graph?depth=` | Explore |
| Search | `POST /v1/search` | Hybrid search |
| Concepts | `POST /v1/concepts:resolve` | Concept → product → site resolution |
| Site metadata | `GET /v1/environments/{e}/applications`, `/fields`, `/schema/objects`, `/relationships` | Verified metadata |
| Analysis | `POST /v1/code:analyze` | JBC analysis |
| Explain | `POST /v1/explanations` | Calculation/decision explanation with evidence |
| Mapping | `POST /v1/mappings:suggest`, `POST /v1/mappings/{id}:validate`, `POST /v1/mappings/{id}:sql` | RIS Sense |
| Testing | `POST /v1/rules:search`, `POST /v1/test-scenarios:generate` | EKWORLD ekSense |
| Impact | `POST /v1/impact:analyze`, `POST /v1/schemas:compare` | Upgrade / schema change |
| Review | `GET /v1/change-sets`, `POST /v1/change-sets/{id}/decisions`, `POST /v1/packages/{p}/versions` (publish) | Studio only (human sessions) |
| Consumer registration | `POST /v1/consumer-artifacts` | RIS/EKWORLD register what they generated, for impact tracking |
| Contributions | `POST /v1/contributions` | Specialist knowledge notes |
| Questions | `GET /v1/open-questions`, `POST /v1/open-questions/{id}/answers` | Knowledge gaps |

## 3. Key contracts (examples)

### 3.1 Resolve a concept against a product and site

```http
POST /v1/concepts:resolve
X-Gnosis-Tenant: NLB
X-Gnosis-Environment: NLB/T24-PRIMARY/UAT
X-Gnosis-Role: lending_aa_specialist
X-Gnosis-Purpose: REPORTING

{ "concept": "outstanding principal", "context": {"product_line": "LENDING"},
  "resolve_to": ["PRODUCT","SITE_FIELDS","EXPORT_COLUMNS"], "max_candidates": 3 }
```

```json
{
  "status": "OK",
  "knowledge_state": "VERIFIED_ONLY",
  "result": {
    "concept": {"key":"concept:lending.outstanding_principal","name":"Outstanding Principal",
                "definition":"Disbursed capital not yet repaid, excluding interest, fees and penalties…",
                "variants":["CURRENT_PRINCIPAL","DUE_PRINCIPAL","OVERDUE_PRINCIPAL"],"status":"VERIFIED"},
    "product_realisation": [{
        "key":"tt:concept:aa.principal_balance_types","status":"VERIFIED",
        "summary":"Principal is held as AA balance types of the ACCOUNT property, split by lifecycle stage.",
        "release_applicability":"R19–R23"}],
    "site_resolution": [{
        "rank":1,"status":"VERIFIED",
        "fields":["site:t24:EB.CONTRACT.BALANCES>TYPE.SYSDATE","site:t24:EB.CONTRACT.BALANCES>OPEN.BALANCE"],
        "export_columns":["site:sql:T24EXP.EXP_ECB_BALANCE_TYPE.BALANCE_TYPE","site:sql:T24EXP.EXP_ECB_BALANCE_TYPE.OPEN_BALANCE"],
        "site_rules":["site:rule:nlb.principal_balance_types"],
        "join_path":[{"from":"T24EXP.EXP_AA_ARRANGEMENT.ARRANGEMENT_ID","to":"T24EXP.EXP_ECB_BALANCE_TYPE.CONTRACT_ID","basis":"SPECIALIST_APPROVED"}]
    }]
  },
  "evidence": [{"evidence_id":"ev-81…","source_title":"Svarm practice note: AA principal balances","basis":"SPECIALIST_APPROVED","disclosure":"VERBATIM_ALLOWED"},
               {"evidence_id":"ev-12…","source_title":"Transact AA user guide (R22)","locator":{"section":"…"},"basis":"DOCUMENTATION","disclosure":"SYNTHESIS_ONLY"}],
  "assumptions": [],
  "questions": [],
  "package_versions": {"banking-core":"3.2.0","temenos-transact":"1.4.1","NLB/T24-PRIMARY/UAT":"0.7.0"},
  "trace_id":"4bf92f35…"
}
```

### 3.2 Analyse a supplied JBC routine

```http
POST /v1/code:analyze
{ "language":"JBC", "artifact_id":"art-55…",            // or "source_inline" (≤ 256 KB) – stored as artefact
  "environment":"NLB/T24-PRIMARY/UAT",
  "outputs":["IR","CODE_FACTS","BUSINESS_RULES","CONCEPT_LINKS"],
  "propose_change_set": true }
→ 202 Accepted  Location: /v1/jobs/job-9a…
```

Job result: `{ ir: {...doc 03 §6.1}, facts:[…], interpretations:[…status CANDIDATE…], unresolved:[…], change_set_id: "cs-…" }`

### 3.3 Suggest source-to-target mappings (RIS Sense)

```http
POST /v1/mappings:suggest
{ "target": {"report":"CRR_LOANS","fields":[
      {"name":"PRINCIPAL_OUTSTANDING","type":"DECIMAL(19,2)","description":"Outstanding principal per loan, reporting date"},
      {"name":"DAYS_PAST_DUE","type":"INTEGER","description":"Days the oldest unpaid instalment is overdue"}]},
  "legacy": {"code_unit":"site:code:NLB.LOAN.EXTRACT"},          // optional legacy evidence
  "grain":"arrangement", "as_of":"BUSINESS_DATE", "dialect":"oracle" }
```

Response: per target field, `mapping` (DSL), `status` (`PROPOSED`), `confidence_drivers` (which evidence bases support it), `validation` (pre-run `validate_mapping`), `financial_significance`, `requires_review_by` (roles), and `sql_preview`. RIS Sense stores the proposal. Once a specialist approves it in Studio, RIS calls `POST /v1/consumer-artifacts` and then submits it to RIS Core.

### 3.4 Find business rules relevant to test generation (EKWORLD)

```http
POST /v1/rules:search
{ "anchor": {"ui_screen":"site:ui:infinity.loan_repayment"} ,   // or api_operation / concept / application
  "categories":["VALIDATION","CALCULATION","STATE_TRANSITION"], "include_expected_values": true }
```

```http
POST /v1/test-scenarios:generate
{ "rule_ids":["rule:lending.partial_repayment_allocation", "site:rule:nlb.repayment_order"],
  "categories":["POSITIVE","NEGATIVE","BOUNDARY","AUTHORIZATION"],
  "target_runner":"EKWORLD", "step_library_version":"ek-steps-2.3" }
```

Returns Gherkin text, plus `trace` (scenario → rule → evidence), `data_requirements` (synthetic data specs), and `unbound_steps`.

### 3.5 Compare schemas and assess affected mappings

```http
POST /v1/impact:analyze
{ "change": {"type":"SNAPSHOT_DIFF","from_snapshot":"snp-12","to_snapshot":"snp-19"},
  "consumers":["RIS","EKWORLD"] }
```

```json
{ "summary":{"changed_entities":42,"financial":3,"affected_artifacts":17},
  "items":[{"entity":"site:sql:T24EXP.EXP_ECB_BALANCE_TYPE.OPEN_BALANCE","change":"TYPE NUMBER(18,2)→NUMBER(19,4)","materiality":"FINANCIAL",
            "affected":[{"consumer":"RIS","external_ref":"rptdef:CRR_LOANS#PRINCIPAL_OUTSTANDING","dependency_role":"SOURCE_FIELD"},
                        {"consumer":"EKWORLD","external_ref":"tc:LN-REG-0042","dependency_role":"EXPECTED_RESULT"}],
            "recommended_action":"REVALIDATE_MAPPING"}] }
```

## 4. MCP tool interface

An MCP server (`gnosis-mcp`, streamable HTTP transport) is a thin adapter over the same service layer. Authentication uses OAuth 2.1 per MCP authorisation spec, mapped to the same principals and policies. Tool names equal skill ids. Input schemas equal the skill `input_schema`. Context headers become required tool arguments, validated identically.

```json
{
  "name": "resolve_source_fields",
  "description": "Resolve a banking concept or reporting requirement to verified site-specific source fields and join paths for one environment. Read-only. Returns evidence and open questions.",
  "inputSchema": {
    "type":"object",
    "required":["environment","concept_or_requirement"],
    "properties":{
      "environment":{"type":"string","description":"e.g. NLB/T24-PRIMARY/UAT"},
      "concept_or_requirement":{"type":"string"},
      "target_grain":{"type":"string","enum":["customer","account","arrangement","transaction","gl_line"]},
      "knowledge_state":{"type":"string","enum":["VERIFIED_ONLY","INCLUDE_PROVISIONAL"],"default":"VERIFIED_ONLY"}
    }
  }
}
```

Exposed tools: all knowledge-access skills, `resolve_source_fields`, `analyze_legacy_code` (propose only), `generate_data_mapping`, `generate_sql`, `validate_mapping`, `generate_test_scenarios`, `explain_decision`, `compare_schema_versions`. **Not exposed:** review decisions, publication, source registration/approval, scan skills that touch client systems (these are started from Studio by an authorised human).

## 5. Outbound integration contracts (GnosisLync → products)

| Call | Direction | Contract |
|---|---|---|
| RIS T24 metadata | GnosisLync → RIS | Wrapped by `t24.ris` adapter. **[ASSUMPTION]** RIS provides list-applications / get-fields API. Contract test pinned to RIS version |
| RIS Core submission | RIS Sense → RIS Core | Unchanged. GnosisLync does not call RIS Core |
| EKWORLD discovery output | GnosisLync → EKWORLD | `GET /ekworld/v1/discoveries/{id}` **[ASSUMPTION]** |
| EKWORLD regression execution | GnosisLync CI → EKWORLD | See doc 09 §4 |
| Package published webhook | GnosisLync → RIS, EKWORLD | `POST {consumer}/hooks/gnosis` `{event:"PACKAGE_PUBLISHED", package, version, impact_report_url}` signed with HMAC-SHA256 |
| Knowledge invalidated webhook | GnosisLync → consumers | `{event:"ARTIFACTS_NEED_REVIEW", items:[external_ref…], reason}` |
