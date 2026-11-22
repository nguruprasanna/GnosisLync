-- GnosisLync migration 0001 — core knowledge model (docs/architecture/02-knowledge-data-model.md)
--
-- Conventions
--   * Executed by the schema owner (migration role). The application connects as a LOGIN role that is a
--     member of `gnosis_app`, which never owns tables and never has BYPASSRLS.
--   * Tenant isolation: every tenant-scoped table has RLS. Policies read transaction-local settings that
--     gnosis.context.db sets from the authenticated principal:
--        gnosis.principal        non-empty when a request context is active (else: zero rows everywhere)
--        gnosis.tenant_ids       comma-separated tenant UUIDs the principal may access
--        gnosis.can_write_shared 'true' if principal may write shared (tenant_id IS NULL) knowledge
--   * Executed verbatim through the raw psycopg connection with no parameters (no placeholder parsing).

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'gnosis_app') THEN
    CREATE ROLE gnosis_app NOLOGIN NOBYPASSRLS;
  END IF;
END $$;

-- ===================================================================== context helpers
CREATE FUNCTION gnosis_ctx_active() RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT coalesce(current_setting('gnosis.principal', true), '') <> ''
$$;

CREATE FUNCTION gnosis_ctx_tenants() RETURNS uuid[]
LANGUAGE sql STABLE AS $$
  SELECT CASE WHEN coalesce(current_setting('gnosis.tenant_ids', true), '') = '' THEN '{}'::uuid[]
              ELSE string_to_array(current_setting('gnosis.tenant_ids', true), ',')::uuid[] END
$$;

CREATE FUNCTION gnosis_ctx_can_write_shared() RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT coalesce(current_setting('gnosis.can_write_shared', true), '') = 'true'
$$;

-- ===================================================================== shared catalogues
CREATE TABLE licence_policy (
  licence_id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name                 text NOT NULL,
  owner                text NOT NULL,
  allowed_tenants      uuid[],
  cross_client_reuse   boolean NOT NULL,
  disclosure_policy    text NOT NULL CHECK (disclosure_policy IN
                       ('VERBATIM_ALLOWED','SHORT_QUOTE_WITH_ATTRIBUTION','SYNTHESIS_ONLY','INTERNAL_ONLY')),
  attribution_text     text,
  external_llm_allowed boolean NOT NULL,
  contract_ref         text
);

CREATE TABLE product (
  product_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code         text UNIQUE NOT NULL,
  vendor       text NOT NULL,
  pack_module  text NOT NULL
);

CREATE TABLE product_release (
  release_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id   uuid NOT NULL REFERENCES product,
  code         text NOT NULL,
  ordinal      int  NOT NULL,
  released_on  date,
  UNIQUE (product_id, code),
  UNIQUE (product_id, ordinal)
);

CREATE TABLE entity_kind (
  kind            text PRIMARY KEY,
  allowed_scopes  text[] NOT NULL,
  attr_schema     jsonb NOT NULL DEFAULT '{}',
  pack            text NOT NULL
);

CREATE TABLE relation_kind (
  kind         text PRIMARY KEY,
  from_kinds   text[] NOT NULL,
  to_kinds     text[] NOT NULL,
  cross_scope  boolean NOT NULL,
  attr_schema  jsonb NOT NULL DEFAULT '{}'
);

-- ===================================================================== tenancy & topology
CREATE TABLE tenant (
  tenant_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code             text UNIQUE NOT NULL,
  name             text NOT NULL,
  deployment_mode  text NOT NULL CHECK (deployment_mode IN ('SHARED','DEDICATED')),
  llm_policy_ref   text NOT NULL,
  data_region      text NOT NULL,
  created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE site (
  site_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  uuid NOT NULL REFERENCES tenant,
  code       text NOT NULL,
  name       text NOT NULL,
  UNIQUE (tenant_id, code)
);

CREATE TABLE system_instance (
  instance_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES tenant,
  site_id      uuid NOT NULL REFERENCES site,
  product_id   uuid NOT NULL REFERENCES product,
  code         text NOT NULL,
  edition      text,
  UNIQUE (tenant_id, code)
);

CREATE TABLE environment (
  environment_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid NOT NULL REFERENCES tenant,
  instance_id      uuid NOT NULL REFERENCES system_instance,
  code             text NOT NULL CHECK (code ~ '^[A-Z0-9_-]+$'),
  env_class        text NOT NULL CHECK (env_class IN ('PRODUCTION','UAT','TEST','DEVELOPMENT','DR')),
  release_id       uuid NOT NULL REFERENCES product_release,
  release_detail   text,
  runtime          text CHECK (runtime IN ('TAFC','TAFJ','OTHER')),
  db_platform      text,
  enabled_modules  text[] NOT NULL DEFAULT '{}',
  discovery_policy jsonb NOT NULL DEFAULT '{}',
  valid_from       timestamptz NOT NULL DEFAULT now(),
  valid_to         timestamptz,
  UNIQUE (instance_id, code, valid_from)
);

CREATE TABLE source_system (
  source_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenant,
  environment_id  uuid NOT NULL REFERENCES environment,
  kind            text NOT NULL CHECK (kind IN ('T24_RIS_METADATA','JBASE','SQL','XMLJSON_STORE','WEB_APP',
                                                'API','DOC_REPOSITORY','CODE_REPOSITORY')),
  name            text NOT NULL,
  connection_ref  text,
  config          jsonb NOT NULL DEFAULT '{}',
  data_class      text NOT NULL DEFAULT 'METADATA_ONLY'
                  CHECK (data_class IN ('METADATA_ONLY','SYNTHETIC','MASKED','PRODUCTION_DATA')),
  approved_by     uuid,
  approved_at     timestamptz,
  status          text NOT NULL DEFAULT 'PENDING_APPROVAL'
                  CHECK (status IN ('PENDING_APPROVAL','APPROVED','SUSPENDED','RETIRED')),
  -- connection_ref must be a secrets-manager reference, never an inline credential
  CHECK (connection_ref IS NULL OR connection_ref ~ '^vault://')
);

-- ===================================================================== artefacts & evidence
CREATE TABLE source_artifact (
  artifact_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid REFERENCES tenant,
  scope            text NOT NULL CHECK (scope IN ('GENERIC','PRODUCT','SITE','SVARM_INTERNAL')),
  artifact_type    text NOT NULL,
  title            text NOT NULL,
  product_id       uuid REFERENCES product,
  release_from     int,
  release_to       int,
  environment_id   uuid REFERENCES environment,
  object_key       text NOT NULL,
  sha256           bytea NOT NULL CHECK (length(sha256) = 32),
  mime_type        text,
  licence_id       uuid NOT NULL REFERENCES licence_policy,
  classification   text NOT NULL CHECK (classification IN ('PUBLIC','INTERNAL','CONFIDENTIAL','RESTRICTED')),
  contains_pii     text NOT NULL DEFAULT 'UNKNOWN' CHECK (contains_pii IN ('NO','YES_REDACTED','YES','UNKNOWN')),
  supersedes_id    uuid REFERENCES source_artifact,
  ingest_status    text NOT NULL DEFAULT 'RECEIVED',
  retention_until  date,
  uploaded_by      uuid NOT NULL,
  uploaded_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE NULLS NOT DISTINCT (tenant_id, sha256),
  CHECK (scope <> 'SITE' OR tenant_id IS NOT NULL)
);

CREATE TABLE evidence (
  evidence_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid REFERENCES tenant,
  artifact_id      uuid NOT NULL REFERENCES source_artifact,
  locator          jsonb NOT NULL,
  excerpt_enc      bytea,
  excerpt_sha256   bytea NOT NULL CHECK (length(excerpt_sha256) = 32),
  extracted_by     text NOT NULL,
  created_at       timestamptz NOT NULL DEFAULT now()
);

-- ===================================================================== knowledge entities
CREATE TABLE knowledge_entity (
  entity_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid REFERENCES tenant,
  scope            text NOT NULL CHECK (scope IN ('GENERIC','PRODUCT','SITE','SVARM_INTERNAL')),
  kind             text NOT NULL REFERENCES entity_kind,
  canonical_key    text NOT NULL,
  product_id       uuid REFERENCES product,
  environment_id   uuid REFERENCES environment,
  domain_codes     text[] NOT NULL DEFAULT '{}',
  merged_into      uuid REFERENCES knowledge_entity,
  created_at       timestamptz NOT NULL DEFAULT now(),
  CHECK ((scope = 'SITE') = (tenant_id IS NOT NULL AND environment_id IS NOT NULL)),
  CHECK (scope <> 'GENERIC' OR tenant_id IS NULL),
  UNIQUE NULLS NOT DISTINCT (tenant_id, environment_id, kind, canonical_key)
);

-- Lifecycle status domain shared by entity and relation versions (doc 02 §4.1)
CREATE DOMAIN knowledge_status AS text CHECK (VALUE IN
  ('DRAFT','CANDIDATE','OBSERVED','UNDER_REVIEW','PROVISIONAL','VERIFIED','REJECTED','SUPERSEDED','DEPRECATED','INCORRECT'));
CREATE DOMAIN verification_basis AS text CHECK (VALUE IN
  ('SOURCE_METADATA','CODE_OBSERVED','DOCUMENTATION','SPECIALIST_APPROVED','DATA_PROFILED','AI_INFERRED'));
CREATE DOMAIN financial_significance AS text CHECK (VALUE IN ('NONE','LOW','MEDIUM','HIGH'));

-- ===================================================================== review (needed before versions for FKs)
CREATE TABLE change_set (
  change_set_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid REFERENCES tenant,
  origin         text NOT NULL,
  title          text NOT NULL,
  status         text NOT NULL DEFAULT 'OPEN' CHECK (status IN
                 ('OPEN','IN_REVIEW','APPROVED','PARTIALLY_APPROVED','REJECTED','PUBLISHED','WITHDRAWN')),
  risk_level     text NOT NULL DEFAULT 'NONE',
  required_roles text[] NOT NULL DEFAULT '{}',
  created_by     uuid,
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE review_decision (
  review_decision_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id            uuid REFERENCES tenant,
  change_set_id        uuid NOT NULL REFERENCES change_set,
  change_item_id       uuid,
  reviewer_id          uuid NOT NULL,
  reviewer_role        text NOT NULL,
  decision             text NOT NULL CHECK (decision IN ('APPROVE','APPROVE_PROVISIONAL','REJECT','REQUEST_CHANGES')),
  rationale            text NOT NULL CHECK (length(btrim(rationale)) > 0),
  corrected_version_id uuid,
  signed_hash          bytea NOT NULL CHECK (length(signed_hash) = 32),
  decided_at           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE entity_version (
  version_id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id              uuid NOT NULL REFERENCES knowledge_entity,
  tenant_id              uuid REFERENCES tenant,
  version_no             int  NOT NULL CHECK (version_no >= 1),
  status                 knowledge_status NOT NULL,
  name                   text NOT NULL,
  definition             text,
  attributes             jsonb NOT NULL DEFAULT '{}',
  release_from           int,
  release_to             int,
  epistemic_class        text NOT NULL CHECK (epistemic_class IN
                         ('FACT','INTERPRETATION','IMPLEMENTATION_SPECIFIC','ASSUMPTION')),
  verification_basis     verification_basis,
  financial_significance financial_significance NOT NULL DEFAULT 'NONE',
  confidence             real CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
  sensitivity            text NOT NULL DEFAULT 'INTERNAL',
  produced_by            text NOT NULL,
  change_set_id          uuid REFERENCES change_set,
  review_decision_id     uuid REFERENCES review_decision,
  created_by             uuid,
  created_at             timestamptz NOT NULL DEFAULT now(),
  content_hash           bytea NOT NULL CHECK (length(content_hash) = 32),
  embedding              vector(1024),
  search_tsv             tsvector GENERATED ALWAYS AS
                         (to_tsvector('simple', coalesce(name, '') || ' ' || coalesce(definition, ''))) STORED,
  UNIQUE (entity_id, version_no),
  CHECK (release_from IS NULL OR release_to IS NULL OR release_from <= release_to)
);
CREATE INDEX entity_version_embedding_idx ON entity_version USING hnsw (embedding vector_cosine_ops);
CREATE INDEX entity_version_tsv_idx       ON entity_version USING gin (search_tsv);
CREATE INDEX entity_version_name_trgm_idx ON entity_version USING gin (name gin_trgm_ops);
CREATE INDEX entity_version_tenant_idx    ON entity_version (tenant_id);

CREATE TABLE entity_head (
  entity_id         uuid PRIMARY KEY REFERENCES knowledge_entity,
  tenant_id         uuid REFERENCES tenant,
  verified_version  uuid REFERENCES entity_version,
  working_version   uuid REFERENCES entity_version
);

-- ===================================================================== relations
CREATE TABLE knowledge_relation (
  relation_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid REFERENCES tenant,
  kind           text NOT NULL REFERENCES relation_kind,
  from_entity    uuid NOT NULL REFERENCES knowledge_entity,
  to_entity      uuid NOT NULL REFERENCES knowledge_entity,
  UNIQUE NULLS NOT DISTINCT (tenant_id, kind, from_entity, to_entity),
  CHECK (from_entity <> to_entity)
);
CREATE INDEX knowledge_relation_from_idx ON knowledge_relation (from_entity);
CREATE INDEX knowledge_relation_to_idx   ON knowledge_relation (to_entity);

CREATE TABLE relation_version (
  version_id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  relation_id            uuid NOT NULL REFERENCES knowledge_relation,
  tenant_id              uuid REFERENCES tenant,
  version_no             int NOT NULL CHECK (version_no >= 1),
  status                 knowledge_status NOT NULL,
  verification_basis     verification_basis NOT NULL,
  financial_significance financial_significance NOT NULL DEFAULT 'NONE',
  attributes             jsonb NOT NULL DEFAULT '{}',
  explanation            text,
  confidence             real CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
  produced_by            text NOT NULL,
  change_set_id          uuid REFERENCES change_set,
  review_decision_id     uuid REFERENCES review_decision,
  created_at             timestamptz NOT NULL DEFAULT now(),
  UNIQUE (relation_id, version_no)
);

CREATE TABLE evidence_link (
  evidence_link_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           uuid REFERENCES tenant,
  entity_version_id   uuid REFERENCES entity_version,
  relation_version_id uuid REFERENCES relation_version,
  evidence_id         uuid NOT NULL REFERENCES evidence,
  stance              text NOT NULL CHECK (stance IN ('SUPPORTS','CONTRADICTS','CONTEXT')),
  CHECK (num_nonnulls(entity_version_id, relation_version_id) = 1)
);

CREATE TABLE change_item (
  change_item_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  change_set_id       uuid NOT NULL REFERENCES change_set,
  tenant_id           uuid REFERENCES tenant,
  op                  text NOT NULL CHECK (op IN ('CREATE','UPDATE','DEPRECATE','MARK_INCORRECT','MERGE','LINK','UNLINK')),
  entity_version_id   uuid REFERENCES entity_version,
  relation_version_id uuid REFERENCES relation_version,
  base_version_id     uuid,
  diff                jsonb NOT NULL DEFAULT '{}',
  materiality         text NOT NULL CHECK (materiality IN ('COSMETIC','DESCRIPTIVE','SEMANTIC','FINANCIAL')),
  conflict_with       uuid[] NOT NULL DEFAULT '{}',
  CHECK (num_nonnulls(entity_version_id, relation_version_id) = 1)
);
ALTER TABLE review_decision
  ADD CONSTRAINT review_decision_change_item_fk FOREIGN KEY (change_item_id) REFERENCES change_item;

-- ===================================================================== packages
CREATE TABLE knowledge_package (
  package_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid REFERENCES tenant,
  scope          text NOT NULL CHECK (scope IN ('GENERIC','PRODUCT','SITE','SVARM_INTERNAL')),
  environment_id uuid REFERENCES environment,
  product_id     uuid REFERENCES product,
  code           text NOT NULL,
  UNIQUE NULLS NOT DISTINCT (tenant_id, code)
);

CREATE TABLE package_version (
  package_version_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  package_id         uuid NOT NULL REFERENCES knowledge_package,
  tenant_id          uuid REFERENCES tenant,
  semver             text NOT NULL CHECK (semver ~ '^[0-9]+\.[0-9]+\.[0-9]+$'),
  base_versions      jsonb NOT NULL DEFAULT '{}',
  status             text NOT NULL DEFAULT 'BUILDING'
                     CHECK (status IN ('BUILDING','VALIDATING','VALIDATION_FAILED','PUBLISHED','WITHDRAWN')),
  regression_run_id  uuid,
  published_by       uuid,
  published_at       timestamptz,
  manifest_sha256    bytea,
  UNIQUE (package_id, semver),
  CHECK (status <> 'PUBLISHED' OR (published_by IS NOT NULL AND published_at IS NOT NULL AND manifest_sha256 IS NOT NULL))
);

CREATE TABLE package_member (
  package_version_id  uuid NOT NULL REFERENCES package_version,
  tenant_id           uuid REFERENCES tenant,
  entity_version_id   uuid REFERENCES entity_version,
  relation_version_id uuid REFERENCES relation_version,
  CHECK (num_nonnulls(entity_version_id, relation_version_id) = 1)
);

-- ===================================================================== consumer artefacts
CREATE TABLE consumer_artifact (
  consumer_artifact_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id            uuid NOT NULL REFERENCES tenant,
  consumer             text NOT NULL CHECK (consumer IN ('RIS','EKWORLD','OTHER')),
  external_ref         text NOT NULL,
  artifact_type        text NOT NULL,
  package_version_id   uuid NOT NULL REFERENCES package_version,
  status               text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','NEEDS_REVIEW','RETIRED')),
  UNIQUE (tenant_id, consumer, external_ref)
);

CREATE TABLE artifact_dependency (
  consumer_artifact_id uuid NOT NULL REFERENCES consumer_artifact,
  tenant_id            uuid NOT NULL REFERENCES tenant,
  entity_id            uuid REFERENCES knowledge_entity,
  relation_id          uuid REFERENCES knowledge_relation,
  dependency_role      text NOT NULL,
  CHECK (num_nonnulls(entity_id, relation_id) = 1)
);

-- ===================================================================== discovery
CREATE TABLE discovery_run (
  run_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenant,
  source_id     uuid NOT NULL REFERENCES source_system,
  skill         text NOT NULL,
  status        text NOT NULL DEFAULT 'QUEUED' CHECK (status IN
                ('QUEUED','RUNNING','CHECKPOINTED','SUCCEEDED','PARTIAL','FAILED','CANCELLED')),
  checkpoint    jsonb,
  requested_by  uuid NOT NULL,
  started_at    timestamptz,
  finished_at   timestamptz,
  stats         jsonb,
  error         jsonb
);

CREATE TABLE snapshot (
  snapshot_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id            uuid NOT NULL REFERENCES tenant,
  run_id               uuid NOT NULL REFERENCES discovery_run,
  source_id            uuid NOT NULL REFERENCES source_system,
  object_key           text NOT NULL,
  sha256               bytea NOT NULL CHECK (length(sha256) = 32),
  previous_snapshot_id uuid REFERENCES snapshot,
  diff_summary         jsonb
);

-- ===================================================================== audit (append-only, hash-chained)
CREATE TABLE audit_event (
  seq         bigserial PRIMARY KEY,
  tenant_id   uuid,
  ts          timestamptz NOT NULL DEFAULT now(),
  actor       text NOT NULL,
  actor_role  text,
  action      text NOT NULL,
  object_ref  text,
  details     jsonb NOT NULL DEFAULT '{}',
  prev_hash   bytea NOT NULL,
  hash        bytea NOT NULL
);

-- Canonical row encoding used for hashing; shared by the insert trigger and the verifier.
CREATE FUNCTION gnosis_audit_row_hash(prev bytea, e audit_event) RETURNS bytea
LANGUAGE sql IMMUTABLE AS $$
  SELECT sha256(prev || convert_to(jsonb_build_object(
      'seq', e.seq, 'tenant_id', e.tenant_id, 'ts', to_char(e.ts AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
      'actor', e.actor, 'actor_role', e.actor_role, 'action', e.action,
      'object_ref', e.object_ref, 'details', e.details)::text, 'UTF8'))
$$;

-- SECURITY DEFINER: the chain spans all tenants, so the predecessor must be read regardless of RLS.
CREATE FUNCTION gnosis_audit_chain() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  prev bytea;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtext('gnosis_audit_chain'));
  NEW.ts := now();
  SELECT a.hash INTO prev FROM audit_event a ORDER BY a.seq DESC LIMIT 1;
  NEW.prev_hash := coalesce(prev, '\x'::bytea);
  NEW.hash := gnosis_audit_row_hash(NEW.prev_hash, NEW);
  RETURN NEW;
END $$;

CREATE TRIGGER audit_event_chain BEFORE INSERT ON audit_event
  FOR EACH ROW EXECUTE FUNCTION gnosis_audit_chain();

CREATE FUNCTION gnosis_audit_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'audit_event is append-only' USING ERRCODE = 'insufficient_privilege';
END $$;

CREATE TRIGGER audit_event_no_update BEFORE UPDATE OR DELETE ON audit_event
  FOR EACH ROW EXECUTE FUNCTION gnosis_audit_immutable();
CREATE TRIGGER audit_event_no_truncate BEFORE TRUNCATE ON audit_event
  FOR EACH STATEMENT EXECUTE FUNCTION gnosis_audit_immutable();

-- Returns the first seq whose hash does not verify (NULL = chain intact). Run by an auditor/owner role.
CREATE FUNCTION gnosis_verify_audit_chain() RETURNS bigint
LANGUAGE plpgsql STABLE AS $$
DECLARE
  r audit_event;
  prev bytea := '\x'::bytea;
BEGIN
  FOR r IN SELECT * FROM audit_event ORDER BY seq LOOP
    IF r.prev_hash <> prev OR r.hash <> gnosis_audit_row_hash(prev, r) THEN
      RETURN r.seq;
    END IF;
    prev := r.hash;
  END LOOP;
  RETURN NULL;
END $$;

-- ===================================================================== lifecycle enforcement (doc 02 §4)
-- Mirrors gnosis/knowledge/lifecycle.py. Python validates first (better errors); the DB is the backstop.
CREATE TABLE lifecycle_transition (
  from_status knowledge_status NOT NULL,
  to_status   knowledge_status NOT NULL,
  PRIMARY KEY (from_status, to_status)
);
INSERT INTO lifecycle_transition VALUES
  ('DRAFT','UNDER_REVIEW'),
  ('CANDIDATE','UNDER_REVIEW'),
  ('OBSERVED','VERIFIED'), ('OBSERVED','UNDER_REVIEW'),
  ('UNDER_REVIEW','VERIFIED'), ('UNDER_REVIEW','PROVISIONAL'), ('UNDER_REVIEW','REJECTED'), ('UNDER_REVIEW','CANDIDATE'),
  ('PROVISIONAL','VERIFIED'), ('PROVISIONAL','INCORRECT'), ('PROVISIONAL','SUPERSEDED'), ('PROVISIONAL','DEPRECATED'),
  ('VERIFIED','SUPERSEDED'), ('VERIFIED','DEPRECATED'), ('VERIFIED','INCORRECT');

CREATE FUNCTION gnosis_check_lifecycle() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  old_status text;
  decision   text;
  sig        text := NEW.financial_significance;
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.status NOT IN ('DRAFT','CANDIDATE','OBSERVED') THEN
      RAISE EXCEPTION 'lifecycle: new versions must start as DRAFT, CANDIDATE or OBSERVED (got %)', NEW.status
        USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status = 'OBSERVED' AND NEW.verification_basis IS DISTINCT FROM 'SOURCE_METADATA'
       AND NEW.verification_basis IS DISTINCT FROM 'CODE_OBSERVED' THEN
      RAISE EXCEPTION 'lifecycle: OBSERVED requires basis SOURCE_METADATA or CODE_OBSERVED'
        USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
  END IF;

  -- UPDATE: content is immutable; only lifecycle columns may change. Generated columns (search_tsv) are
  -- excluded because PostgreSQL does not compute them in NEW during BEFORE triggers.
  IF (to_jsonb(NEW) - ARRAY['status','verification_basis','review_decision_id','search_tsv'])
     IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['status','verification_basis','review_decision_id','search_tsv']) THEN
    RAISE EXCEPTION 'lifecycle: version content is immutable; create a new version instead'
      USING ERRCODE = 'check_violation';
  END IF;

  old_status := OLD.status;
  IF NEW.status = old_status THEN
    IF NEW.verification_basis IS DISTINCT FROM OLD.verification_basis
       OR NEW.review_decision_id IS DISTINCT FROM OLD.review_decision_id THEN
      RAISE EXCEPTION 'lifecycle: basis/review may only change together with a status transition'
        USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
  END IF;

  IF NOT EXISTS (SELECT 1 FROM lifecycle_transition t
                 WHERE t.from_status = old_status AND t.to_status = NEW.status) THEN
    RAISE EXCEPTION 'lifecycle: illegal transition % -> %', old_status, NEW.status
      USING ERRCODE = 'check_violation';
  END IF;

  IF NEW.review_decision_id IS NOT NULL THEN
    SELECT d.decision INTO decision FROM review_decision d WHERE d.review_decision_id = NEW.review_decision_id;
  END IF;

  IF NEW.status = 'VERIFIED' THEN
    IF NEW.verification_basis IS NULL OR NEW.verification_basis = 'AI_INFERRED' THEN
      RAISE EXCEPTION 'lifecycle: VERIFIED requires a non-AI verification basis'
        USING ERRCODE = 'check_violation';
    END IF;
    IF decision IS NULL THEN
      -- Deterministic auto-verification: metadata facts that are not financially significant.
      IF NOT (old_status = 'OBSERVED'
              AND NEW.verification_basis IN ('SOURCE_METADATA','CODE_OBSERVED')
              AND sig IN ('NONE','LOW')) THEN
        RAISE EXCEPTION 'lifecycle: VERIFIED requires an APPROVE review decision'
          USING ERRCODE = 'check_violation';
      END IF;
    ELSIF decision <> 'APPROVE' THEN
      RAISE EXCEPTION 'lifecycle: VERIFIED requires decision APPROVE (got %)', decision
        USING ERRCODE = 'check_violation';
    END IF;
  ELSIF NEW.status = 'PROVISIONAL' THEN
    IF decision IS NULL OR decision NOT IN ('APPROVE','APPROVE_PROVISIONAL') THEN
      RAISE EXCEPTION 'lifecycle: PROVISIONAL requires an approval review decision'
        USING ERRCODE = 'check_violation';
    END IF;
  ELSIF NEW.status = 'REJECTED' THEN
    IF decision IS DISTINCT FROM 'REJECT' THEN
      RAISE EXCEPTION 'lifecycle: REJECTED requires a REJECT review decision'
        USING ERRCODE = 'check_violation';
    END IF;
  ELSIF NEW.status IN ('INCORRECT','DEPRECATED') THEN
    -- approving the MARK_INCORRECT / DEPRECATE change item
    IF decision IS DISTINCT FROM 'APPROVE' THEN
      RAISE EXCEPTION 'lifecycle: % requires an APPROVE review decision', NEW.status
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER entity_version_lifecycle BEFORE INSERT OR UPDATE ON entity_version
  FOR EACH ROW EXECUTE FUNCTION gnosis_check_lifecycle();
CREATE TRIGGER relation_version_lifecycle BEFORE INSERT OR UPDATE ON relation_version
  FOR EACH ROW EXECUTE FUNCTION gnosis_check_lifecycle();

-- Review decisions are immutable once recorded.
CREATE FUNCTION gnosis_immutable_row() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% rows are immutable', TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
END $$;
CREATE TRIGGER review_decision_immutable BEFORE UPDATE OR DELETE ON review_decision
  FOR EACH ROW EXECUTE FUNCTION gnosis_immutable_row();
