# 07 — Svarm Banking Knowledge Studio (Frontend)

## 1. Purpose and principles

Knowledge Studio is the specialists' workspace for building, inspecting, correcting and maintaining GnosisLync knowledge. Design principles:

1. **Context is always explicit.** A persistent context bar shows *Tenant › Site › Instance › Environment (Release, Runtime)* or *Generic* / *Product pack (release)*. Switching context is explicit and audited.
2. **Status is always visible.** Every entity, relation and field displays its status chip (VERIFIED, PROVISIONAL, CANDIDATE, CONFLICT, DEPRECATED, INCORRECT) and its verification basis.
3. **Evidence is one click away**, filtered by licence disclosure policy.
4. **Nothing is saved directly to verified knowledge.** Edits create change items, and approval is a separate, attributable act.

Tech: React 18 + TypeScript, Vite, TanStack Router/Query/Table, Cytoscape.js (graph), Monaco (code/IR), react-pdf (evidence viewer with highlight), OIDC PKCE. The Studio calls only the public `/v1` API. There is no privileged backend-for-frontend path.

## 2. Information architecture

```
Studio
├── Home (my review queue, my questions, recent jobs, quality alerts)
├── Acquire
│   ├── Sites & Environments
│   ├── Sources (DB, jBASE/T24, Web URLs, APIs)  — register, preflight, approve
│   ├── Upload (documents, code, specs)          — scope, product, release, licence
│   └── Jobs (ingestion & discovery)              — progress, logs, errors, resume
├── Explore
│   ├── Browse by: Domain | Product/Module/Application | Release | Site/Env | Specialisation | Source system
│   ├── Search (full-text + semantic, facets)
│   ├── Entity page (definition, attributes, versions, evidence, relations, consumers)
│   └── Relationship viewer (graph)
├── Review
│   ├── Change sets (queue, filters by domain/risk)
│   ├── Conflicts
│   ├── Merge proposals
│   └── Open questions
├── Contribute
│   ├── New knowledge (concept, rule, calculation, note, relation)
│   └── Specialist profile
├── Quality (dashboards)
├── Packages (versions, publish, diff, regression results)
└── Admin (roles, licence policies, LLM policies, taxonomy)  — restricted
```

## 3. Key screens

### 3.1 Source registration (Acquire › Sources)

```
┌ Register source ─────────────────────────────────────────────────────────────┐
│ Environment: NLB / T24-PRIMARY / UAT   (Transact R-xx · TAFJ · Oracle)        │
│ Type: (•) SQL database  ( ) T24 via RIS  ( ) jBASE export  ( ) Web app  ( ) API │
│ Name: [NLB T24 export DB (UAT)         ]                                      │
│ Dialect: [Oracle ▾]   Connection: [vault://tenants/nlb/uat/t24exp-ro] (picker)  │
│ Schemas allow-list: [T24EXP] [+]                                              │
│ Data class: (•) METADATA_ONLY ( ) SYNTHETIC ( ) MASKED ( ) PRODUCTION_DATA    │
│ Sampling allowed: [ ] (requires Privacy Officer approval)                     │
│ Profiling allowed: [ ] aggregate-only                                         │
│ [Run preflight]  ✔ connectivity ✔ read-only privileges ✔ 214 objects in scope │
│ [Submit for approval]  → approver: Source Owner (NLB) + Privacy Officer       │
└──────────────────────────────────────────────────────────────────────────────┘
```

Credentials are never typed into Studio. Studio stores only Vault references, which are provisioned out-of-band by tenant admins.

### 3.2 Job monitor

Shows a stage timeline per job (doc 06 §2 / doc 03 §1), counts (objects scanned, entities observed, candidates proposed), and errors grouped by type (`PARSE_LOW_YIELD`, `PRIVILEGE_MISSING`, `RIS_TIMEOUT`, `PII_POLICY_BLOCK`). Available actions: **Resume from checkpoint**, **Retry stage**, **Cancel**, **Open change set**.

### 3.3 Entity page

```
┌ Outstanding Principal  [VERIFIED · SPECIALIST_APPROVED · HIGH financial] v3 ──┐
│ Generic › Lending › Balances                                                 │
│ Definition (Svarm): Disbursed capital not yet repaid, excluding …             │
│ Variants: current · due · overdue                                            │
│ ─ Realisations ──────────────────────────────────────────────────────────── │
│ Temenos Transact (R19–R23): AA principal balance types   [VERIFIED]         │
│ NLB/T24-PRIMARY/UAT: EB.CONTRACT.BALANCES balance lines   [VERIFIED]         │
│   ↳ export T24EXP.EXP_ECB_BALANCE_TYPE.OPEN_BALANCE …         [VERIFIED]         │
│   ↳ UI Infinity "Loan Overview › Principal Outstanding"   [CANDIDATE] ⚑      │
│   ↳ API GET /loans/{id} outstandingPrincipal              [CANDIDATE]        │
│ ─ Rules (4) ─ Calculations (1) ─ Evidence (9) ─ Versions (3) ─ Consumers (6) │
│ Consumers: RIS CRR_LOANS#PRINCIPAL_OUTSTANDING · EKWORLD LN-REG-0042 …        │
│ [Propose edit] [Add note] [Mark deprecated] [Mark incorrect] [Explain]       │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 3.4 Relationship viewer

Cytoscape graph centred on an entity. Node shape encodes layer (generic / product / site / UI / API / code / doc). Edge style encodes status: solid for verified, dashed for candidate, red for contradicts. Filters cover depth (1–4), relation kinds, status and layer. Selecting an edge shows its explanation, evidence and verification basis, with *Approve*, *Reject* or *Edit* actions that add the edge to the current change set.

### 3.5 Change-set review

```
┌ Change set CS-2026-1182 · DISCOVERY: NLB UAT export scan · risk HIGH ────────┐
│ Required approvers: core_banking_data_specialist, lending_aa_specialist (2)   │
│ Filter: [materiality ▾] [kind ▾] [status ▾]       38 items · 3 FINANCIAL       │
│ ┌──────────────────────────────────────────────────────────────────────────┐ │
│ │ ⚠ FINANCIAL  LINK realised_by                                              │ │
│ │ EXP_LOAN_BAL.PRINCIPAL_OS → concept:lending.outstanding_principal         │ │
│ │ Basis: CODE_OBSERVED (NLB.LOAN.EXTRACT L55–66) + AI_INFERRED explanation   │ │
│ │ Evidence ▸ IR derivation  ▸ export DDL  ▸ practice note                    │ │
│ │ Counter-evidence ▸ derivation includes '*ACCOUNT' wildcard: may include    │ │
│ │                    non-principal balance types (e.g. suspense) — check     │ │
│ │ (o) Approve  (o) Approve provisional  (o) Reject  (o) Request changes      │ │
│ │ Rationale: [__________________________________________] (required)         │ │
│ │ [Edit before approving]                                                     │ │
│ └──────────────────────────────────────────────────────────────────────────┘ │
│ [Approve selected (non-financial only)]  [Submit decisions]                   │
└──────────────────────────────────────────────────────────────────────────────┘
```

Rules shown in the UI and enforced by the API: bulk approval is disabled for FINANCIAL items, and a rationale is mandatory. When four-eyes applies, the same person cannot provide both approvals. Every decision is signed (doc 02 `review_decision.signed_hash`).

### 3.6 Contribute knowledge

Templated forms per kind (concept, rule with DSL editor and live parse, calculation with formula and worked example, practice note, relation). Each form has a "generalisable beyond this client?" switch. Selecting it routes the contribution through IP/confidentiality review (doc 08 §6).

### 3.7 Quality dashboard (§15.E)

| Widget | Metric / query |
|---|---|
| Coverage heatmap | Domain × Product: % of concept-area facets with VERIFIED knowledge (coverage model, doc 06 §6) |
| Unverified discoveries | Count of CANDIDATE by age bucket, domain, environment |
| Conflicts | Open `contradicts` by severity |
| Missing source relationships | Site fields used by consumers without verified lineage. Export columns without `exported_as` |
| Outdated knowledge | Entities whose evidence artefacts were superseded. Product knowledge without release range |
| Upgrade changes | Per environment release change: diff items pending review |
| Corrections awaiting approval | Change items from human edits pending second approval |
| Failures | Ingestion/discovery failures (24h/7d) by error code |
| Consumer impact | Consumer artefacts in `NEEDS_REVIEW` |

## 4. User journeys

### J1 — Onboard a new site (Admin + Data Specialist)
1. Admin creates *Site* and *Instance* and adds *Environment* UAT with release, runtime and DB.
2. Data Specialist registers the T24-via-RIS source and the SQL export source. Preflight runs and the sources go for approval. The NLB source owner approves.
3. The specialist starts the **Site enrichment plan** (`enrich_site_knowledge`): discover applications/fields → scan export DB → discover schema → relationships → JBC analysis of uploaded routines → correlation.
4. The specialist monitors the jobs, then opens the resulting change sets.

### J2 — Review discoveries (Domain Specialist)
1. Home shows "12 FINANCIAL items in Lending awaiting you".
2. The specialist opens the change set, inspects evidence and the graph, edits one mapping explanation, approves 9, rejects 2 with rationale and requests changes on 1.
3. A second approver completes the four-eyes approvals.

### J3 — Publish a package (Knowledge Steward)
1. Packages › NLB/T24-PRIMARY/UAT › "Build 0.8.0" from approved items.
2. The knowledge regression runs (doc 09 §6). Results show 0 unexpected changes to approved calculations and 2 affected EKWORLD tests, which are re-run and pass.
3. The steward publishes. RIS and EKWORLD receive the webhook.

### J4 — Capture expertise (T24 Consultant)
1. The consultant opens the `ACCOUNT>ONLINE.ACTUAL.BAL` entity page.
2. They choose **Add note**, explain the difference from `WORKING.BALANCE` at this site, link both fields, and mark the note site-specific.
3. The note goes to review. Once approved, it appears in every RIS/EKWORLD explanation that touches either field.

### J5 — Handle an upgrade (Data Specialist)
1. Admin records the UAT upgrade to a new release (new environment row).
2. Rediscovery runs, and the diff shows 42 changes, 3 of them FINANCIAL.
3. The impact view lists 17 affected consumer artefacts. Specialists review the changes, and the old approved knowledge stays served for PROD until UAT is approved and PROD is upgraded.

## 5. Access control in the UI

The UI hides actions the principal cannot perform, but **all enforcement is server-side**. Tenant switching requires the tenant in the token. Restricted evidence shows a lock, the licence name and the reason.
