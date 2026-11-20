# 09 — Quality Gates, Testing and Knowledge Regression

**Engineering rule:** every change must be secure, testable, traceable and reviewable before it can affect approved banking knowledge or production behaviour. This applies equally to code, prompts, role configs, skill manifests and knowledge.

## 1. What counts as a "change"

| Change type | Lives in | Gate path |
|---|---|---|
| Application code, adapters, parsers | Git | Code gates (§3) |
| Prompts, role profiles, skill manifests, LLM policies | Git (`prompts/`, `roles/`, `skills/`, `policies/`) | Code gates + **prompt evaluation** (§2.4) |
| Taxonomy, product-pack seed knowledge | Git (`packs/*/seed/`) and DB | Code gates + knowledge regression |
| Site knowledge (discoveries, approvals) | DB (change sets) | Review workflow + knowledge regression (§6) before package publication |

## 2. Test framework and coverage policy

### 2.1 Stack

`pytest`, `pytest-xdist`, `hypothesis` (property-based), `pytest-postgresql`/Testcontainers (real Postgres + pgvector), `respx`/`vcrpy` (HTTP), `schemathesis` (OpenAPI contract fuzzing), `pact-python` (consumer contracts with RIS Sense/EKWORLD), `vitest` + Playwright component tests for Studio, `behave`-compatible Gherkin files executed by EKWORLD.

LLM-dependent unit tests use a **recorded-response fake provider** keyed by prompt hash. Tests are deterministic and never call real models. Real-model behaviour is covered by evaluation suites (§2.4).

### 2.2 Test markers

```
@pytest.mark.unit            fast, no I/O
@pytest.mark.db              real Postgres (testcontainers)
@pytest.mark.contract        OpenAPI/Pact
@pytest.mark.security        authz, isolation, PII, guards
@pytest.mark.knowledge       knowledge-model invariants
@pytest.mark.eval            LLM evaluation (nightly / PR when prompts change)
```

### 2.3 Coverage policy

| Area | Line | Branch | Additional |
|---|---|---|---|
| Overall | ≥ 80 % | ≥ 70 % | Diff coverage on changed lines ≥ 90 % (`diff-cover`) |
| `gnosis.knowledge.lifecycle`, `gnosis.review`, `gnosis.context`, AI Gateway transforms/policy, RLS helpers, runner guards | ≥ 95 % | ≥ 90 % | Mutation testing (`mutmut`) score ≥ 80 % nightly |
| Banking calculations in packs (`packs/banking_core/calc`) | 100 % | 100 % | Property tests + golden worked examples approved by specialists |
| Parsers (JBC, XML/JSON inference) | ≥ 90 % | ≥ 80 % | Fuzzing (`atheris`) nightly. Corpus of real (sanitised) routines |

Required unit-test themes (CI checks that tests with these markers exist for touched modules, using a `CODEOWNERS`-like `testmap.yaml`): expected behaviour, invalid inputs, calculation correctness, metadata parsing, relationship inference constraints (no auto-promotion), tenant isolation, PII detection/redaction, authorisation decisions, release compatibility.

Example tests:

```python
def test_candidate_cannot_transition_directly_to_verified(lifecycle, candidate_version):
    with pytest.raises(IllegalTransition):
        lifecycle.transition(candidate_version, to="VERIFIED", basis="AI_INFERRED")

@given(iban=valid_ibans())
def test_gateway_pseudonymises_every_valid_iban(gateway, iban):
    out = gateway.transform(f"Customer paid from {iban} yesterday", policy=NLB_POLICY)
    assert iban not in out.text and re.search(r"<IBAN_[A-Z2-7]{10}>", out.text)

def test_rls_hides_other_tenant_site_fields(db, tenant_a, tenant_b, field_of_b):
    with db.as_tenant(tenant_a):
        assert db.get_entity(field_of_b.entity_id) is None
        assert field_of_b.entity_id not in [r.entity_id for r in db.knn(field_of_b.embedding, k=50)]

def test_release_out_of_range_knowledge_flagged(retrieval, env_r19, field_valid_r22_onwards):
    res = retrieval.product_field("ACCOUNT>X.FIELD", env=env_r19)
    assert res.status == "NOT_APPLICABLE_FOR_RELEASE"
```

### 2.4 Prompt / skill evaluation

Every LLM-assisted skill has an evaluation set in `evals/<skill>/` with labelled cases built from approved knowledge. Metrics are per skill, for example `extract_business_rules` precision/recall vs specialist labels, citation validity rate (must be 100 % after validators), and hallucinated-reference rate. A prompt or model change that reduces any metric beyond its tolerance fails the PR gate. A model/provider change is treated as a change and must pass the same evaluations.

## 3. Git and CI/CD gates

### 3.1 Local (pre-commit, developer convenience — **not** a security control)

```yaml
# .pre-commit-config.yaml (abridged)
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    hooks: [{id: ruff}, {id: ruff-format}]
  - repo: https://github.com/pre-commit/mirrors-mypy
    hooks: [{id: mypy, args: [--strict]}]
  - repo: https://github.com/gitleaks/gitleaks
    hooks: [{id: gitleaks}]
  - repo: local
    hooks:
      - {id: affected-unit-tests, name: affected unit tests, entry: scripts/test_affected.py --marker "unit or knowledge", language: system, pass_filenames: false}
      - {id: skill-manifest-lint, entry: scripts/lint_skills.py, language: system, files: ^skills/}
      - {id: gherkin-lint, entry: gherkin-lint, language: node, files: \.feature$}
      - {id: ekworld-smoke, entry: scripts/ekworld_smoke.sh, language: system, stages: [pre-push]}  # if EKWORLD reachable
```

### 3.2 Pull request (enforced server-side; required status checks on protected `main`)

```yaml
# .github/workflows/pr.yml  (CI platform: [ASSUMPTION] GitHub Actions; equivalent for GitLab/Azure DevOps)
name: pr-gates
on: pull_request
jobs:
  static:      {steps: [ruff, mypy --strict, import-linter, semgrep --config p/python --config .semgrep/gnosis.yml]}
  secrets:     {steps: [gitleaks detect --redact]}
  unit:        {steps: ["pytest -m 'unit or knowledge or security' -n auto --cov --cov-branch", diff-cover --fail-under=90]}
  integration: {services: [postgres-pgvector, minio], steps: ["pytest -m 'db or contract'"]}
  contract:    {steps: [schemathesis run openapi.json, pact verify --provider gnosis-api]}
  deps:        {steps: [osv-scanner -r ., pip-audit, npm audit --omit=dev, syft -o cyclonedx, trivy image]}
  evals:       {if: "changed('prompts/**','skills/**','roles/**','policies/llm/**')", steps: [scripts/run_evals.py --gate]}
  isolation-privacy:
               {steps: ["pytest -m security -k 'tenant or pii or gateway or guard'"]}
  ekworld-regression:
               {steps: ["scripts/ekworld_run.py --suite pr --tags '@pr and not @wip' --wait --fail-on failed,error"]}
  knowledge-review-check:
               {steps: [scripts/require_domain_owner.py]}   # changes under packs/**/seed, rules, extraction → domain code owner
```

Branch protection: required checks above, ≥ 1 code-owner approval (2 for `gateway/`, `knowledge/lifecycle`, `review/`, `policies/`), signed commits, linear history, no force-push, dismissal of stale approvals. **AI-generated code** goes through the same path. PR template has a mandatory "AI assistance used" field, which is informational, not an exemption.

### 3.3 Release

| Gate | Content |
|---|---|
| Full regression | All EKWORLD suites (`@regression`), full eval suites, Studio E2E |
| Non-functional | Load (k6) on retrieval/resolve endpoints, gateway latency budget, discovery throughput |
| Security & privacy | DAST (ZAP) against staging, container scan, pen-test findings ≤ agreed threshold, privacy scenario suite |
| Resilience | Provider-outage drill, DB failover, interrupted-discovery recovery, backup restore test (last ≤ 90 days) |
| Deployment | Blue/green or canary with automatic rollback on SLO breach. DB migrations expand/contract (backward compatible for one release) |
| Approval | Release manager + security + product owner sign-off in the release record. Evidence pushed to the Compliance Evidence Registry |

## 4. EKWORLD integration (regression execution platform)

GnosisLync **generates and maintains** test specifications. EKWORLD **executes** them using its existing Playwright/Robot automation.

### 4.1 Gherkin repository structure

```
tests/gherkin/
├── README.md                          conventions, tag taxonomy
├── step-library.lock                  pinned EKWORLD step-library version (e.g. ek-steps 2.3.1)
├── platform/                          tests of GnosisLync itself
│   ├── privacy/
│   │   ├── llm_pii_protection.feature
│   │   └── jbc_sensitive_literals.feature
│   ├── isolation/cross_bank_access.feature
│   ├── knowledge/
│   │   ├── release_selection.feature
│   │   ├── inferred_not_verified.feature
│   │   ├── approved_knowledge_protection.feature
│   │   └── schema_change_impact.feature
│   ├── resilience/
│   │   ├── llm_provider_outage.feature
│   │   └── discovery_interruption_recovery.feature
│   └── calculations/ai_calculation_guard.feature
├── banking/                           generated from approved knowledge (generic + product)
│   └── lending/repayment_allocation.feature
├── sites/
│   └── NLB/T24-PRIMARY/               site-specific (tenant-confidential repo or path with restricted access)
├── defects/                           one scenario per fixed defect: DEF-<id>.feature
└── steps/                             GnosisLync-specific step definitions contributed to EKWORLD library
    ├── gateway_steps.py               (e.g. "the outgoing AI request must not contain …")
    ├── knowledge_steps.py
    └── api_steps.py
```

Tag taxonomy: `@req:<id>`, `@rule:<canonical_key>`, `@defect:<id>`, `@concept:<key>`, `@category:{positive|negative|boundary|privacy|authz|regression|resilience}`, `@gate:{commit|pr|release}`, `@env:<env>`, `@generated` (LLM-generated, requires `@reviewed-by:<user>` before it may carry `@gate:*`). A CI linter fails if any scenario lacks a traceability tag (`@req`, `@rule` or `@defect`) or if `@generated` lacks `@reviewed-by`.

### 4.2 Generated-scenario lifecycle

`generate_test_scenarios` → scenario stored as `test_case_ref` entity (CANDIDATE) → specialist review in Studio (expected results checked against VERIFIED rules) → PR into `tests/gherkin/` → step binding check (all steps bound in the pinned step library, else `UNBOUND_STEP` fails CI) → executable.

### 4.3 Execution API contract (proposed; **[ASSUMPTION]** to be agreed with EKWORLD team)

```http
POST /ekworld/v1/suites:run
Authorization: Bearer <ci-service-token>
{
  "project": "gnosislync",
  "source": {"repo":"git@…/gnosislync.git","ref":"<commit-sha>","paths":["tests/gherkin/platform"]},
  "tag_expression": "@gate:pr and not @wip",
  "environment": "gnosis-staging-synthetic",
  "step_library": "ek-steps@2.3.1",
  "parameters": {"gnosis_base_url":"https://gnosis-staging…","gateway_inspection_url":"https://gw-staging…/inspect"},
  "callback_url": "https://ci…/hooks/ekworld",
  "correlation_id": "pr-412-run-3"
}
→ 202 {"run_id":"ekr-7781","status":"QUEUED"}

GET /ekworld/v1/runs/ekr-7781
→ {"run_id":"ekr-7781","status":"COMPLETED","summary":{"passed":61,"failed":1,"skipped":2,"error":0},
   "scenarios":[{"feature":"platform/privacy/llm_pii_protection.feature","name":"Prevent customer PII…",
                 "tags":["@req:GL-REQ-19.1","@category:privacy"],"status":"FAILED",
                 "failed_step":"And no direct customer identifiers must reach the LLM",
                 "artifacts":{"report":"…","log":"…"}}],
   "report_scrubbed": true}
```

Requirements on EKWORLD reports: scrubbed of sensitive data (no payload dumps, masked screenshots), retained as compliance evidence (registry `evidence_type = TEST_RUN`).

### 4.4 Gateway inspection for privacy scenarios

The staging AI Gateway runs with an **inspection sink**: a test-only provider stub (`provider: inspect-stub`) that records outbound payloads to a synthetic-data-only store, readable via `GET /inspect/requests?correlation_id=`. This makes "inspect the outgoing AI request at the gateway boundary" executable. The sink is impossible to enable in production (build flag + policy validation refuses `inspect-stub` when `env_class = PRODUCTION`).

## 5. Mandatory scenario catalogue (platform)

```gherkin
@req:GL-REQ-19.1 @category:privacy @gate:pr
Feature: Banking knowledge privacy protection

  Background:
    Given the synthetic site "SYN-BANK" with environment "SYN/T24/TEST" exists
    And the AI Gateway uses the inspection provider

  Scenario: Prevent customer PII from reaching an external LLM
    Given a site database contains customer personal data
      | customer_id | name           | iban                   |
      | SYN000123   | Ana Testna     | SI56 1910 0000 0123 438 |
    And the external LLM policy requires anonymisation
    When a knowledge discovery skill requests customer data
    Then the AI Gateway must detect sensitive fields
    And personal data must be removed or safely transformed
    And no direct customer identifiers must reach the LLM
    And the request must be blocked if protection fails
    And the decision must be recorded in the audit log

  Scenario: Fail closed when the PII detector is unavailable
    Given the PII detector is unavailable
    When the skill "extract_domain_knowledge" sends a document section to the LLM
    Then the request must be rejected with code "PRIVACY_CHECK_UNAVAILABLE"
    And the inspection provider must have received 0 requests
```

```gherkin
@req:GL-REQ-10.1 @category:authz @gate:pr
Feature: Tenant isolation
  Scenario Outline: Unauthorised cross-bank knowledge access
    Given user "<user>" is entitled only to tenant "<own>"
    When the user requests site field "<field>" of tenant "<other>" via <channel>
    Then the response status must be <status>
    And the response must not reveal whether the field exists
    And an access-denied audit event must be recorded
    Examples:
      | user  | own   | other | field                            | channel        | status |
      | u-syn | SYN-A | SYN-B | EB.CONTRACT.BALANCES>OPEN.BALANCE | REST           | 404    |
      | u-syn | SYN-A | SYN-B | EB.CONTRACT.BALANCES>OPEN.BALANCE | semantic search| 200 with 0 results |
      | u-syn | SYN-A | SYN-B | EB.CONTRACT.BALANCES>OPEN.BALANCE | MCP tool       | 404    |
```

Additional mandatory features (one-line intent; full text in repo):

| Feature | Key assertions |
|---|---|
| Incorrect core banking release selected | `X-Gnosis-Release` ≠ env release → `409 RELEASE_MISMATCH`. Knowledge outside release range is never returned as applicable |
| JBC routine containing sensitive values | Literals with IBAN/password are pseudonymised/redacted before LLM. Source owner alerted. IR keeps placeholders |
| Schema change invalidating approved mappings | Snapshot diff → affected RIS mapping set `NEEDS_REVIEW`. Package publication blocked until resolved or accepted |
| AI-generated incorrect financial calculation | Mapping with calculation diverging from VERIFIED rule fails `validate_mapping`. Cannot be approved without HIGH-authority reviewer + second approver |
| Inferred relationship incorrectly marked as verified | API/DB reject `CANDIDATE→VERIFIED`. `AI_INFERRED` basis cannot be VERIFIED without review decision |
| Unauthorised modification of approved knowledge | Direct UPDATE of VERIFIED version denied (DB role). API edit creates a new version in a change set. Audit chain intact |
| LLM provider outage or timeout | Circuit opens. Fallback only to equal/higher trust provider. Deterministic endpoints keep answering. No silent quality drop (`llm_features_unavailable` flag) |
| Recovery after interrupted discovery | Kill runner mid-scan → resume from checkpoint. No duplicate entities. Snapshot hash equals uninterrupted run on same source |

## 6. Knowledge regression and impact analysis

### 6.1 Golden knowledge assertions

When an item is VERIFIED with `financial_significance ≥ MEDIUM`, the platform compiles it into **executable knowledge assertions**:

| Item | Compiled assertion |
|---|---|
| Calculation | Worked examples (inputs → expected output) approved by the specialist, evaluated by the pack's reference implementation |
| Mapping | Expected SQL AST fingerprint. Validation checks. Reconciliation totals on a synthetic fixture dataset |
| Business rule | Rule AST. Linked Gherkin scenarios (`tested_by`) |
| Relation (join) | Cardinality and key columns |
| Concept realisation | Set of site fields/columns |

### 6.2 Regression run (triggered by: new docs ingested, release change, rediscovery, rule correction, pipeline/prompt/model change)

```mermaid
flowchart TB
  T[Trigger] --> A[1 Identify affected entities<br/>direct + reverse traversal of relations]
  A --> B[2 Identify dependents<br/>consumer_artifact via artifact_dependency:<br/>RIS mappings, report defs, EKWORLD tests]
  B --> C[3 Re-interpret<br/>re-run extraction/correlation on new inputs<br/>in shadow mode (no writes to heads)]
  C --> D[4 Compare with approved<br/>content_hash + typed attribute diff + assertion evaluation]
  D --> E{Unexpected change?}
  E -- none --> P[5 Validations pass → package candidate]
  E -- descriptive --> R1[Review: any steward]
  E -- semantic/financial --> R2[Review: domain specialist, four-eyes for HIGH]
  R1 & R2 --> S[6 Select/generate regression scenarios<br/>tags @rule/@concept of affected items → EKWORLD run]
  S --> P
  P --> Q[7 Publish package version<br/>SemVer bump; webhook; consumer artefacts flagged]
```

Hard guarantees:
- Shadow re-interpretation **never** writes to `entity_head.verified_version`. Changes surface only as change items with `base_version_id`.
- A package version cannot reach `PUBLISHED` if any golden assertion of a VERIFIED item fails, unless that item is itself changed in the same package by an approved FINANCIAL change item. In that case the dependent consumer artefacts are listed in the release notes and set to `NEEDS_REVIEW`.
- An expected test result in EKWORLD derived from knowledge is tied to the package version. A change in expected value requires the same approval as the underlying rule change.

### 6.3 Impact query (core of `gnosis.impact`)

```sql
WITH RECURSIVE changed(entity_id, depth) AS (
  SELECT unnest(:changed_entity_ids::uuid[]), 0
  UNION
  SELECT CASE WHEN r.to_entity = c.entity_id THEN r.from_entity ELSE r.to_entity END, c.depth + 1
  FROM changed c
  JOIN knowledge_relation r ON (r.to_entity = c.entity_id OR r.from_entity = c.entity_id)
  JOIN relation_kind rk ON rk.kind = r.kind AND rk.kind IN
       ('derived_from','exported_as','instance_of','realised_by','implements_rule','governs','joins_to','maps_to')
  WHERE c.depth < :max_depth
)
SELECT ca.consumer, ca.external_ref, ad.dependency_role, min(c.depth) AS distance
FROM changed c
JOIN artifact_dependency ad ON ad.entity_id = c.entity_id
JOIN consumer_artifact ca  ON ca.consumer_artifact_id = ad.consumer_artifact_id
WHERE ca.status = 'ACTIVE'
GROUP BY 1,2,3 ORDER BY distance;
```
