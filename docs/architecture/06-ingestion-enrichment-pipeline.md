# 06 — Source Ingestion, Knowledge Extraction and Reconciliation Pipeline

## 1. Goal

Turn uploaded guides, help texts, manuals, configuration documents, code and specifications into **cumulative, taxonomy-organised, evidence-linked knowledge**. Each upload *enriches* the single knowledge model. Uploads never create per-document knowledge bases.

## 2. Pipeline stages

```mermaid
flowchart LR
  U[Upload / connector] --> G0[0 Admission<br/>licence, scope, classification,<br/>malware scan, dedupe sha256]
  G0 --> G1[1 Parse & normalise<br/>layout-aware, tables, code blocks,<br/>CHM/HTML help, OCR fallback]
  G1 --> G2[2 Privacy scan<br/>PII/secrets detection,<br/>redact before any LLM use]
  G2 --> G3[3 Segment & classify<br/>domain, product, module, application,<br/>release, topic type]
  G3 --> G4[4 Index<br/>chunks: FTS + embeddings<br/>(retrieval of evidence)]
  G3 --> G5[5 Extract<br/>concepts, rules, calcs, relations,<br/>terms, product entities]
  G5 --> G6[6 Resolve & consolidate<br/>entity linking to existing knowledge,<br/>dedupe, merge proposals]
  G6 --> G7[7 Conflict & gap analysis<br/>contradictions, missing knowledge,<br/>specialist questions]
  G7 --> G8[8 Synthesis<br/>Svarm-language definitions,<br/>overlap filter]
  G8 --> CS[Change set → Studio review]
  CS --> PUB[Publish package version<br/>after knowledge regression]
```

Each stage is an idempotent job step keyed by `(artifact_sha256, stage, stage_version)`. Re-running after a pipeline upgrade only recomputes changed stages. Stage versions are part of `produced_by`.

| Stage | Implementation | LLM | Failure handling |
|---|---|---|---|
| 0 Admission | Licence must be chosen (no default). ClamAV scan. sha256 dedupe → link to existing artefact. `supersedes` detection by title/version heuristics + user confirmation | No | Reject with reason. Quarantine on malware |
| 1 Parse | `docling`/`unstructured` for PDF/DOCX (layout, tables), BeautifulSoup for HTML/CHM-extracted help, Tika fallback, Tesseract OCR when text yield < threshold. Code files → language-specific parsers | No | Parse QA metrics (`text_yield`, `ocr_conf`, `table_count`). Below threshold → `NEEDS_TRIAGE` |
| 2 Privacy scan | Presidio + custom recognisers (doc 08 §3). Findings stored as spans. Redacted text is the only text that continues to LLM stages | No | If `RESTRICTED` and policy disallows → stop before stage 3. Notify privacy officer |
| 3 Segment & classify | Heading-based segmentation (fallback: semantic segmentation by embedding drift). Classifier: rules first (T24 application name dictionary from product pack, regexes such as `[A-Z]{2,}(\.[A-Z0-9]+)+` for T24 identifiers) and then LLM for domain/topic type when rules are inconclusive | Partly | Unclassified sections → triage queue |
| 4 Index | Chunk ≤ 800 tokens with section path. Store as `doc_chunk` (FTS + vector). Chunks are **evidence**, not knowledge | Embedding (local model) | Retry |
| 5 Extract | `extract_domain_knowledge`: schema-constrained extraction per section, role-framed by detected domain | Yes | Schema-invalid → one repair attempt → drop + log |
| 6 Consolidate | Entity linking (§4) | Partly | Ambiguous link → merge question |
| 7 Conflict & gap | `detect_knowledge_conflicts` + coverage model (§6) | Partly | — |
| 8 Synthesis | Rewrite definitions in Svarm voice from *all* evidence for the entity. Overlap filter | Yes | Filter failure → re-synthesise once, else flag for human |

## 3. Taxonomy (organising principle)

Knowledge is organised by the **Svarm Banking Taxonomy**, not by document structure:

```
Domain (GL, CUSTOMER, DEPOSITS, LENDING, INTEREST_FEES, PAYMENTS, FX, EOD, RECON, REGULATORY, RUNTIME, DATA)
 └─ Concept area (e.g. LENDING › Repayment › Allocation)
     └─ Concept  ←realised_by─ Product concept / application / field  ←instance_of─ Site entity
         ├─ Business events
         ├─ Rules / calculations / validations
         └─ Terms (synonyms, multilingual labels)
Technical specialisations (orthogonal): TAFC, TAFJ, jBC, jBASE, PL/SQL, Java customisation, APIs
```

The taxonomy itself is knowledge (`banking_domain`, `banking_concept` with `part_of`) and is maintained by the Knowledge Steward. Product packs may add product-specific areas, but they must anchor them to a generic concept or explicitly mark them `PRODUCT_ONLY`.

## 4. Entity linking and consolidation

For each extracted item (concept, rule, product entity):

1. **Exact key match:** T24 identifiers (`ACCOUNT>WORKING.BALANCE`) and canonical keys.
2. **Lexical:** trigram + synonym-expanded term match over names and terms.
3. **Semantic:** embedding kNN over synthesised definitions (top 10).
4. **LLM adjudication** for top candidates: `SAME | NARROWER | BROADER | RELATED | DIFFERENT`, with justification.

Outcomes:

| Adjudication | Action |
|---|---|
| SAME, compatible content | Attach new evidence to existing entity. Propose *enrichment* version only if new information is present (attribute-level diff) |
| SAME, incompatible content | `CONFLICT` item (§5) |
| NARROWER/BROADER | New entity + `part_of`/`broader_than` relation candidate |
| DIFFERENT / no match | New CANDIDATE entity |
| Same entity, different release | Parallel version with release range (doc 02 §7), not a conflict |

Merge of duplicates is a reviewed operation (`MERGE` change item): the loser gets `merged_into`. Relations and evidence are re-pointed in the new version, and the old key remains resolvable as an alias.

## 5. Conflict detection

| Conflict type | Detection | Example |
|---|---|---|
| Attribute contradiction | Deterministic compare of typed attributes (day-count basis, rounding, MV flag) | Doc A: Actual/365. Doc B: Actual/360 for same product condition |
| Rule contradiction | Rule-AST comparison: same condition, different effect | "Accrual continues for NAB" vs "stops for NAB" |
| Release divergence (not a conflict) | Same entity, disjoint release ranges | — |
| Site vs product | Site observation contradicts product default | Product: field mandatory. Site: optional (local override) → recorded as `IMPLEMENTATION_SPECIFIC`, not a conflict |
| Textual contradiction | LLM NLI-style check over definitions of the same entity | — |

Conflicts become `contradicts` relations and Studio conflict cards. **They are never auto-resolved.** VERIFIED knowledge stays served until a reviewer resolves the conflict. The conflict is surfaced as a warning to consumers that touch the entity.

## 6. Gap detection and specialist questions

A **coverage model** per concept area defines expected facets, e.g. for a `calculation`: inputs, formula, day-count, rounding, currency handling, business-date dependency, exceptions, product realisation, site realisation, and test coverage. Missing or CANDIDATE-only facets produce `open_question` entities:

```json
{"kind":"open_question","name":"Rounding of AA interest accrual at NLB",
 "attributes":{"about":"site:rule:nlb.interest_accrual","facet":"rounding",
   "question":"Which rounding rule applies to daily interest accrual for consumer loans at NLB (UAT R-xx)? Product documentation describes configurable rounding; no site configuration was discovered.",
   "suggested_role":"lending_aa_specialist","blocking_consumers":["RIS:CRR_LOANS"],"priority":"HIGH"}}
```

Questions are prioritised by blocking consumer demand. Answers are recorded as `practice_note` or `site_rule` contributions and linked with `answers`.

## 7. Svarm-owned representation and IP protection

- **Definitions are synthesised** (stage 8) from all evidence of an entity, in Svarm's terminology. The prompt instructs abstraction and restructuring. Third-party phrasing is not to be reproduced.
- **Overlap filter (deterministic):** 8-gram overlap between the synthesised text and each evidence excerpt. If the longest shared span exceeds 25 tokens, or the overlap ratio exceeds 0.2, and the source licence is `SYNTHESIS_ONLY`/`INTERNAL_ONLY`, the text is rejected and re-synthesised. On repeated failure it is flagged for human writing. Short identifiers (field names, application names, API paths) are whitelisted because they are facts, not expression.
- **Provenance retained internally:** every version links to evidence. `licence_policy.attribution_text` is added to API responses when the licence requires attribution. Responses never present third-party knowledge as a Svarm discovery: `evidence[].basis` and `source_title` are always returned (title disclosure itself may be restricted by `disclosure_policy = INTERNAL_ONLY`, in which case the response says "licensed product documentation").
- **Excerpt exposure:** verbatim excerpts are returned only for `VERBATIM_ALLOWED`. `SHORT_QUOTE_WITH_ATTRIBUTION` allows ≤ 200 characters with attribution.
- **Licence scoping:** documentation licensed to a client (e.g. Temenos docs provided under NLB's licence) is ingested with `allowed_tenants = [NLB]`. Knowledge *derived* from it is reusable across clients only if `cross_client_reuse = true`, as decided by Svarm legal for each licence. Otherwise derived entities carry `tenant_id` and remain client-scoped. **[ASSUMPTION: legal review of Temenos/Oracle documentation licence terms is required before Phase 1 ingestion.]**

## 8. Code ingestion specifics

- JBC: one artefact per routine/insert file. `$INSERT` resolution uses the site's insert set first, then the product-pack standard inserts for the release.
- Secrets/PII in code (hard-coded passwords, test customer names, IBANs in literals) are detected in stage 2 and replaced with typed placeholders (`<SECRET_1>`, `<IBAN_1>`) before LLM interpretation. Detection events are raised to the source owner.
- Code is `CONFIDENTIAL` by default, and code-derived knowledge is always tenant-scoped.

## 9. Human contributions as a first-class source

`POST /v1/contributions` (from Studio) creates a `source_artifact` of type `SPECIALIST_NOTE`, authored by the specialist. The extraction pipeline runs on it with `verification_basis = SPECIALIST_APPROVED` *once reviewed by a second qualified reviewer* for financially significant content. Example: a T24 consultant explaining why `ACCOUNT>WORKING.BALANCE` and `ACCOUNT>ONLINE.ACTUAL.BAL` differ at a site. This becomes a `practice_note` linked to both fields, with `epistemic_class = IMPLEMENTATION_SPECIFIC` and site scope, or Svarm-wide scope if marked generalisable and cleared (doc 08 §6).
