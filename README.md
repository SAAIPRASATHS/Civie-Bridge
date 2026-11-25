# CivicBridge AI

> Autonomous multi-agent system for citizen grievance resolution and public service delivery.
> Built for the **AgentVerse Finale** — fully local LLM inference via **Ollama** + **LangGraph**.

CivicBridge AI ingests citizen grievances (English / Tamil, text + photo evidence),
classifies and de-duplicates them, routes them to the responsible municipal
department, drafts resolution recommendations, watches SLA deadlines, and keeps a
human officer in the loop for every consequential decision.

> **Status: Phase 6 complete — the six agents now run as one observable,
> stateful LangGraph workflow** (`POST /api/v1/workflow/run`), with per-node
> timing, bounded retries, an in-process checkpointer for human-review
> interrupts and the guarantee that no consequential action happens
> automatically. Phases 7–10 (auth/RBAC, the web applications, analytics and
> evaluation) are still to come.

---

## 1. Current state (Phases 1–6)

| Phase | Delivered and verified |
|---|---|
| **1 — Environment** | Repository structure, `.env` configuration, PostgreSQL 17 database + least-privilege role, SQLAlchemy access layer, Ollama integration, `python -m app.cli health` → **7 passed / 0 failed / 0 skipped** |
| **2 — Database and dataset** | SQLAlchemy 2.0 models for all **10 tables**, **11 native PG enum types**, one reversible Alembic migration (`0255b8fcf1e2`), a **1,050-complaint** synthetic dataset (English / Tamil / Tamil-transliterated, duplicates planted) and a prototype department directory + service guidelines |
| **3 — Agents** | **Complaint Intake Agent** and **Classification Agent**, Pydantic result schemas, three FastAPI endpoints, DB persistence with one agent-execution log per step, and 49 agent tests |
| **4 — Agents + integration** | **Duplicate Detection Agent** (offline TF-IDF + Haversine; 70/30 text/geography; category guard) and **Evidence Verification Agent** (OpenCV measurements only), Pydantic schemas, an offline benchmark harness (`ml/duplicate_detection/`), **four new FastAPI endpoints**, the human-approval link-review workflow and **31 duplicate-detection tests** |
| **5 — Routing and resolution** | **Department Routing Agent** (deterministic rule-match over the prototype directory) and **Resolution Recommendation Agent** (prototype service guidelines + unverified SLA disclosure), Pydantic schemas, **four new FastAPI endpoints**, the officer assignment-approval workflow and **47 routing/resolution tests** |
| **6 — Orchestration** | **LangGraph workflow** wiring the six agents into one observable run (typed Pydantic state, per-node timing, bounded retries and timeouts, degraded-mode handling, a human-review checkpoint with an in-process checkpointer), a **versioned `/api/v1/workflow` endpoint pair** and **25 orchestration tests** |

| Area | What exists now |
|---|---|
| Repository structure | `backend/`, `agents/`, `ml/`, `datasets/`, `tests/`, `docs/`, frontend placeholder |
| Configuration | `.env` / `.env.example`, Pydantic Settings — model swap requires **no code change** |
| Database | `civicbridge` database + least-privilege `civicbridge_app` role (`backend/scripts/init_db.sql`) |
| DB schema | 10 tables, 11 native PG enum types, one reversible Alembic migration (`backend/alembic/`) |
| DB access layer | SQLAlchemy 2.0 engine/session factory + typed connectivity probe (`app/database/session.py`) |
| Synthetic dataset | 1,050 complaints, 4 categories, 3 languages, planted duplicates (`datasets/`) |
| Reference data | Prototype department directory + configurable service guidelines (`app/datasets/reference_data.py`) |
| Database CLI | `python -m app.cli generate-dataset` / `seed-db` / `dataset-report` |
| Intake Agent | Language detection, structured extraction, grounding guards (`app/agents/intake.py`) |
| Classification Agent | 4-category classification, priority, department routing, human-review flagging (`app/agents/classification.py`) |
| Duplicate Detection Agent | Offline TF-IDF + Haversine matching, ranked potential matches with scores and shared terms (`app/agents/duplicate.py`) |
| Evidence Verification Agent | OpenCV measurements (sharpness, exposure, contrast, edge density, colour); never proof, capped at `uncertain` (`app/agents/evidence.py`) |
| Department Routing Agent | Deterministic rule-match over the prototype directory; recommended department, matched rules, missing information, review flag (`app/agents/routing.py`) |
| Resolution Recommendation Agent | Prototype service guidelines, unverified SLA disclosed as a prototype estimate, suggested next steps and required evidence (`app/agents/resolution.py`) |
| Agent plumbing | Shared Ollama client with a bounded response cache, deterministic extraction, JSON repair loop, persistence (`app/agents/`) |
| Orchestration workflow | One typed, stateful **LangGraph** run over the six agents with conditional edges, per-node timing, bounded retries/timeouts, degraded-mode handling and a human-review checkpoint (`app/agents/workflow.py`) |
| Orchestration state | `WorkflowState` (identity, inputs, every agent result, review requirements, errors/degradation, trace + timings) and `WorkflowRunResult` for the API (`app/schemas/workflow.py`) |
| API | `POST /api/agents/intake`, `POST /api/agents/classify`, `POST /api/agents/analyze-complaint` (`app/api/agents.py`); `POST /api/duplicates/analyze`, `GET /api/complaints/{id}/duplicates`, `POST /api/complaints/{id}/duplicates/review` (`app/api/duplicates.py`); `POST /api/evidence/analyze` (`app/api/evidence.py`); `POST /api/routing/recommend`, `GET /api/complaints/{id}/routing`, `POST /api/complaints/{id}/routing/decision`, `POST /api/resolution/recommend` (`app/api/routing.py`); `POST /api/v1/workflow/run`, `POST /api/v1/workflow/{id}/resume` (`app/api/workflow.py`) |
| Agent CLI | `python agents/intake_agent.py`, `python agents/classification_agent.py` |
| Ollama integration | Health probe, model discovery, model verification, LangChain chat-model factory, live smoke test (`app/services/ollama_service.py`) |
| Tests | **226 tests** across 11 files (`tests/`), service-gated so they skip cleanly when PostgreSQL or Ollama is absent |

Nothing in the project fabricates model output. If Ollama or the model is
missing, the agents degrade to deterministic extraction (`degraded=True`)
instead of inventing data, and every model claim about a location, landmark or
duration is checked against the citizen's own words before it is used.

---

## 2. The agents (Phases 3–6)

All six agents live in `backend/app/agents/` and are importable independently,
so the FastAPI app and the LangGraph orchestration workflow share exactly one
copy of each agent. `agents/intake_agent.py` and `agents/classification_agent.py`
are thin runnable wrappers around the same code.

### 2.1 Intake → Classification (Phase 3)

```
Citizen complaint
      │
      ▼
┌──────────────────┐   validated     ┌───────────────────────┐   final complaint
│  Intake Agent    │───── JSON ─────▶│ Classification Agent  │──── analysis ───▶ human
│  (language,      │                 │ (category, priority,  │                  review
│   duration,      │  IntakeResult   │  department, action)  │   ClassificationResult
│   location)      │                 │                       │
└──────────────────┘                 └───────────────────────┘
        │                                     │
        └──────────── persisted to `complaints` + one `agent_execution_logs` row per step ────────┘
```

**Architecture: deterministic first, model second.** Language detection,
duration/ward/location/landmark extraction, keyword classification, life-safety
escalation and department routing are all plain Python. The local model is an
enrichment layer on top:

* **At most two model calls per complaint** — one per agent, plus at most
  `AGENT_MAX_REPAIR_ATTEMPTS` schema-repair calls, with a bounded cache so
  identical prompts are never inferred twice.
* **Repairable model output.** Ollama's JSON mode constrains syntax, not schema,
  so a fenced reply or an object where an array was asked for (observed on
  `missing_information`) is normalised before validation. Only a genuinely
  unusable response costs the repair call and, failing that, the deterministic
  fallback.
* **Grounded output.** A value the model volunteers about a location, landmark
  or duration is discarded unless it is traceable to the citizen's own words
  (`unsupported_details_dropped` records every rejection).
* **No invented routing.** `department_code`, `department_name`, `guideline_code`
  and `sla_hours` are read from `DEPARTMENT_DIRECTORY` for the chosen category;
  the model-facing schema has no department field at all.
* **Escalate-only priority, bounded by evidence.** The model may raise the
  priority but never lower it. `CRITICAL`, however, requires corroborating
  severity evidence in the complaint itself: without it the model's escalation
  is capped at `HIGH` and the complaint goes to a human, so neither the model's
  opinion nor its confidence score can decide a critical priority on its own.
  Life-safety terms (accident, electrocution, live wire, drowning, collapse, …)
  force critical urgency whatever the category.
* **The priority decision is auditable.** Every result carries
  `deterministic_priority` (the band the keyword evidence supports),
  `model_priority` (what the model recommended, recorded even when refused),
  the applied `priority` and an `escalation_reason` when the two differ.
* **Low confidence is visible.** Below `AGENT_CONFIDENCE_THRESHOLD`, on any
  model/deterministic disagreement, or on an escalation the evidence did not
  support, the result is flagged `needs_human_review=True` with a
  `review_reason`.

#### Endpoints

| Method | Path | Returns |
|---|---|---|
| `POST` | `/api/agents/intake` | `IntakeResult` — language, normalised description, duration, location, missing information, clarification questions |
| `POST` | `/api/agents/classify` | `ClassificationResult` — category, subcategory, priority with its audit trail, urgency, department, SLA, review flag |
| `POST` | `/api/agents/analyze-complaint` | `ComplaintAnalysis` — both results; `"persist": true` also writes the complaint and its agent logs |

`/classify` accepts either raw `text` (the intake agent runs first) or a
previously produced `intake` object, so a caller that has already called
`/intake` never pays for the same model call twice.

### 2.2 Duplicate Detection Agent (Phase 4)

`app/agents/duplicate.py` compares one complaint against a set of others and
returns **potential** matches, ranked. It is deliberately offline and
deterministic — no embeddings download, no per-pair LLM call:

* **Text signal — TF-IDF cosine similarity.** A scikit-learn `TfidfVectorizer`
  using `char_wb` 3–5 grams, chosen because character n-grams stay robust across
  English, Tamil and Tamil-transliterated text (Tamil typed in Latin letters).
  `shared_terms` lists the overlapping terms so a match is explainable.
* **Geographic signal — Haversine distance.** 0 m scores 1.0, decaying to 0.0 at
  `GEOGRAPHY_RADIUS_M` (2,000 m). A complaint with **no coordinates is not
  disqualified**: the geographic weight is simply redistributed onto the text
  comparison, which then has to clear the higher `TEXT_ONLY_THRESHOLD`.
* **Weighted score.** With coordinates on both sides the combined score is
  `0.70 · text + 0.30 · geography`; the constants (`TEXT_WEIGHT`,
  `GEOGRAPHY_WEIGHT`, `DUPLICATE_SCORE_THRESHOLD = 0.62`,
  `TEXT_ONLY_THRESHOLD = 0.80`, `GEOGRAPHY_RADIUS_M`) are module-level and
  overridable.
* **Category safeguard.** Category is a *guard*, not a weighted signal: a
  cross-category pair has its score multiplied by `CATEGORY_MISMATCH_PENALTY`
  (0.85) — an initial configurable safeguard to keep unrelated categories from
  matching on wording alone.
* **Borderline cases only** may consult `llama3.1:latest` for a second opinion;
  everything else is decided by the arithmetic above.
* **Human approval is mandatory and structurally enforced.**
  `linking_requires_human_approval` is always `true` on the result, and the
  agent itself never writes `duplicate_of_id`. It reports potential matches; it
  never declares two complaints definitively identical.

The agent is benchmarked offline by `ml/duplicate_detection/evaluate.py` against
a hand-labelled set (22 pairs) and a planted synthetic set (150 pairs); see
`datasets/` for the frozen benchmark.

Known limitations (documented, not hidden):

* **A Tamil-transliteration pair is a known false negative.** The threshold is
  deliberately *not* lowered to force one example through; the pair is retained
  as a regression case and more transliterated examples are being collected.
* **22 hand-labelled pairs is an initial benchmark, not a production accuracy
  claim.** It is a useful signal and a starting point for a larger held-out set.
* **The `char_wb` vectoriser is the validated configuration.** A char-only vs
  word-only vs combined n-gram comparison is planned as a separate experiment;
  the combined vectoriser will be adopted only if it measurably improves
  precision/recall/F1, particularly for Tamil and transliterated Tamil.
* **The 0.85 cross-category penalty is an initial configurable safeguard**, not
  a final threshold; its effect on false positives is verified before it is
  treated as settled.

### 2.3 Evidence Verification Agent (Phase 4)

`app/agents/evidence.py` measures one uploaded evidence image with **OpenCV
only** and reports what can honestly be established — sharpness (Laplacian
variance), brightness, contrast, dark-pixel ratio, edge density and colour
statistics — separately from any low-confidence interpretation. It is built so
that a photo can never decide a complaint's fate:

* `is_proof_of_validity` is **always `false`** and `verification_status` is
  capped at `uncertain` (or `manual_review_required`).
* `requires_manual_review` is **always `true`**; a human still reviews every
  image.
* It performs **no object, text or scene recognition**. With no vision model
  configured the result says so in its `limitations`, and any claim a model
  would make is recorded as an assumption with a confidence, never as fact
  (`unsupported_claims_dropped` records every rejection).
* Analysing evidence **never validates or rejects a complaint**; the endpoint
  stores the analysis against the complaint but leaves its status untouched.
* The original file is never altered — the analysis carries the image's SHA-256
  `content_digest` so the stored record ties back to the exact bytes.

### 2.4 Human approval workflow (Phase 4)

Duplicate detection recommends; an **officer decides**. That boundary is enforced
in code, not just documented:

```
1. Potential match identified      GET  /api/complaints/{id}/duplicates
        │                          -> ranked matches, each with match_score,
        │                             text_similarity, shared_terms and reasons
        ▼
2. Similarity + explanation shown  (same response; human-readable `reasoning`)
        │
        ▼
3. Officer reviews the match       (out of band — the officer looks at the pair)
        │
        ▼
4. Officer approves or rejects     POST /api/complaints/{id}/duplicates/review
        │                             body: { candidate_id, decision, reviewer_id, reason }
        │
        ▼
5. Decision recorded in audit trail  -> one `complaint_status_history` row per
                                       decision (approve OR reject), with the
                                       reviewer id, the reason and the match score
```

`complaints.duplicate_of_id` is written by **exactly one code path in the whole
application** — the explicit review call — and only on an `approve` decision,
which also sets `duplicate_similarity` and moves the complaint to the
`DUPLICATE` status. A `reject` changes nothing (a no-op status transition) but
still appends its audit row, so both outcomes are fully traceable. There is no
irreversible automatic merging anywhere in the system.

#### Phase 4 endpoints

| Method | Path | Returns |
|---|---|---|
| `POST` | `/api/duplicates/analyze` | `DuplicateDetectionResult` — ranked potential matches for a complaint supplied inline or read from storage |
| `GET` | `/api/complaints/{complaint_id}/duplicates` | `DuplicateMatchesPage` — paginated potential matches for a stored complaint |
| `POST` | `/api/complaints/{complaint_id}/duplicates/review` | `DuplicateReviewResult` — the recorded approve/reject decision (the only writer of `duplicate_of_id`) |
| `POST` | `/api/evidence/analyze` | `EvidenceAnalysisResponse` — OpenCV evidence measurement, optionally stored against a complaint |

Examples:

```powershell
# Analyse an inline complaint against an inline comparison set
curl -X POST http://127.0.0.1:8000/api/duplicates/analyze `
  -H "Content-Type: application/json" `
  -d '{"text":"Garbage has not been collected near the market for five days.",
       "category":"waste_management",
       "candidates":[{"reference_id":"C-2","text":"No garbage collection at the market for five days.",
                      "category":"waste_management"}]}'

# Retrieve paginated potential matches for a stored complaint
curl "http://127.0.0.1:8000/api/complaints/<complaint-id>/duplicates?limit=10&offset=0"

# Officer approves one proposed link (the ONLY path that writes duplicate_of_id)
curl -X POST http://127.0.0.1:8000/api/complaints/<complaint-id>/duplicates/review `
  -H "Content-Type: application/json" `
  -d '{"candidate_id":"<other-complaint-id>","decision":"approve","reason":"same market, same week"}'
```

`/duplicates/analyze` requires at least one of `candidates` or `complaint_id`;
the review endpoint is the sole writer of `duplicate_of_id`; and no endpoint in
Phase 4 ever merges, links or rejects a complaint without that explicit call.

### 2.5 Department Routing Agent (Phase 5)

`app/agents/routing.py` maps a complaint's category, ward and location to a
recommended municipal department. Routing is **deterministic and explainable by
design** — there is no LLM in the decision path:

* **Rule match on the prototype directory.** The department is read from the
  prototype `DEPARTMENT_DIRECTORY` (`app/datasets/reference_data.py`) on an
  exact category match. `matched_rules` records which rule fired, and
  `rationale` explains it in plain language.
* **Location is a signal, not a guess.** A ward is extracted from the explicit
  `ward` field or a `ward`/`zone`/`block`/`division` reference in the text. A
  ward-only complaint is still routable; a complaint with **no ward or
  location** is routable but flagged, with `ward_or_location` in
  `missing_information`.
* **Ambiguity is surfaced, never silently resolved.** Two different ward
  references, or a `ward` field that disagrees with the text, lower the
  confidence score (ambiguity penalty) and set `requires_human_review=True` with
  a `review_reason` — the recommendation is still returned, but explicitly for a
  human to check.
* **Unknown or unmapped categories are not guessed.** An unknown category adds
  `category` to `missing_information`; a known category with **no department
  mapping** adds `department_mapping`. In both cases
  `recommended_department_code` is `None` — the agent never assigns a complaint
  to an unrelated department.
* **Prototype data is labelled.** Every result carries
  `routing_source="prototype_department_directory"`, `is_prototype_data=True`
  and the `DEPARTMENT_PROVENANCE` string; the directory is prototype reference
  data, not a verified municipal assignment.
* **It never writes.** The agent returns a recommendation only; it cannot change
  a complaint's department or status.

The optional local LLM may be used later to draft a supporting explanation, but
it never decides where a complaint is officially assigned.

### 2.6 Resolution Recommendation Agent (Phase 5)

`app/agents/resolution.py` proposes next steps for a complaint from its category
and available evidence, reusing the prototype service guidelines:

* **Guideline-backed, honestly labelled SLA.** When a guideline exists for the
  category the result copies its `sla_hours` and resolution steps, and sets
  `sla_is_verified=False` with the `GUIDELINE_PROVENANCE` disclaimer. The
  `sla_note` states the value is an unverified prototype estimate and is **not**
  an official government commitment.
* **No guideline is explained, not hidden.** With no applicable guideline the
  agent returns a manual-triage recommendation, names the missing
  `category → guideline` mapping and still never resolves or closes anything.
* **Recommendations are distinguished from actions.** `recommendation_only=True`
  and `status_change_applied=False` are always set, and `completed_actions` is
  always empty — the agent lists `suggested_next_steps`, it does not perform
  them. `no_auto_resolution=True` and `requires_human_approval=True` make the
  boundary structural.
* **Evidence gaps are explicit.** `required_evidence` comes from the guideline;
  anything the caller did not supply appears in `missing_evidence`, so an officer
  can see what proof is still outstanding.
* **It never changes status.** Like the routing agent, it writes nothing.

### 2.7 Human approval workflow (Phase 5)

Routing recommends; an **officer decides**. That boundary is enforced in code,
not just documented:

```
1. Recommendation produced        POST /api/routing/recommend            (inline)
        │                         GET  /api/complaints/{id}/routing      (stored)
        │                         -> recommended department, matched
        │                            rules, confidence, missing info
        ▼
2. Officer reviews it             (out of band — the officer checks the
        │                          ward, category and recommendation)
        ▼
3. Officer approves or rejects    POST /api/complaints/{id}/routing/decision
        │                             body: { decision, reviewer_id, officer_id?, reason?, notes? }
        ▼
4. Decision recorded                -> one `complaint_assignments` row (the
                                       recommendation, decision, reviewer and
                                       timestamp) and one `complaint_status_history`
                                       row per decision (approve OR reject)
```

`complaints.current_department_id` is written by **exactly one code path in the
whole application** — this explicit decision call — and only on an `approve`
decision, which also moves the complaint to `ASSIGNED`. A `reject` changes
nothing (a no-op status transition) but still appends its audit row. Only an
officer or administrator may decide (a citizen reviewer is `403`); a complaint
in a terminal or non-assignable status cannot be assigned (`409`); and neither
outcome resolves, rejects or closes the complaint. There is no automatic
assignment anywhere in the system.

#### Phase 5 endpoints

| Method | Path | Returns |
|---|---|---|
| `POST` | `/api/routing/recommend` | `RoutingRecommendation` — recommended department, matched rules, confidence, missing information and review flag for an inline complaint |
| `GET` | `/api/complaints/{complaint_id}/routing` | `RoutingRecommendation` — the routing recommendation for a stored complaint (read-only) |
| `POST` | `/api/complaints/{complaint_id}/routing/decision` | `RoutingDecisionResult` — the recorded approve/reject decision (the only writer of `current_department_id`) |
| `POST` | `/api/resolution/recommend` | `ResolutionRecommendation` — guideline-backed next steps, the unverified SLA disclosure and required/missing evidence |

Examples:

```powershell
# Recommend a department for an inline complaint (deterministic routing)
curl -X POST http://127.0.0.1:8000/api/routing/recommend `
  -H "Content-Type: application/json" `
  -d '{"category":"waste_management","ward":"5",
       "text":"Garbage has not been collected near the market for five days."}'

# Recommend a department for a stored complaint (read-only)
curl "http://127.0.0.1:8000/api/complaints/<complaint-id>/routing"

# Recommend resolution steps, disclosing the prototype (unverified) SLA
curl -X POST http://127.0.0.1:8000/api/resolution/recommend `
  -H "Content-Type: application/json" `
  -d '{"category":"waste_management","available_evidence":["photo"]}'

# Officer approves the routing recommendation (the ONLY path that assigns)
curl -X POST http://127.0.0.1:8000/api/complaints/<complaint-id>/routing/decision `
  -H "Content-Type: application/json" `
  -d '{"decision":"approve","reviewer_id":"<officer-or-admin-uuid>","reason":"ward 5 confirmed"}'
```

The recommend/read endpoints never assign, reject, resolve or close anything;
the decision endpoint is the sole writer of `current_department_id`; and no
Phase 5 endpoint ever takes a consequential action without that explicit call.

### 2.8 LangGraph orchestration (Phase 6)

`app/agents/workflow.py` wires the six agents into **one typed, stateful
LangGraph run**. It imports each agent unchanged; it never re-implements one.
The state is a validated Pydantic model (`WorkflowState`) carrying the identity,
the original text, every agent result, the review requirements, the
errors/degradation flags and a per-node trace with timings.

Eight nodes, connected by conditional edges so an inapplicable step is skipped
rather than forced:

```
START → intake ──(unusable text)──────────────────────────────→ assemble → END
          │
          ├──(far too thin to classify)───────────────────────→ human_review
          └──→ classification ──(failed / undetermined / low confidence)→ human_review
                    │
                    └──→ duplicate_detection ──(likely duplicate)─→ human_review
                              │
                              └──→ evidence_analysis  (only when images are supplied)
                                        │
                                        └──→ routing ──(missing / failed)──→ human_review
                                                │
                                                └──→ resolution ──(needs review)──→ human_review
                                                            │
                                                            └──→ assemble → END
```

| Node | Agent / service | Skipped when |
|---|---|---|
| `intake` | Intake Agent | never (an empty complaint fails the run instead) |
| `classification` | Classification Agent | intake was too thin to classify |
| `duplicate_detection` | Duplicate Detection Agent | no candidate set was supplied |
| `evidence_analysis` | Evidence Verification Agent | no images were supplied (optional) |
| `routing` | Department Routing Agent | an earlier node already required a human |
| `resolution` | Resolution Recommendation Agent | routing was missing |
| `human_review` | checkpoint (records the requirement) | a run that needs no review |
| `assemble` | result assembly | never |

**Reliability.** Each node runs through one primitive that applies a configurable
wall-clock timeout and a bounded number of retries (`_execute`). Only transient
agent/timeout errors are retried; a success returns on the first attempt, so a
retry can never duplicate a side effect, and a programming error fails the node
immediately. A node that still fails marks the run **degraded** and records a
safe error summary — it never invents a result, and the run continues to the
human checkpoint. An unavailable Ollama model degrades to the deterministic
fallbacks the earlier phases already allow.

**Safety (unchanged, and now enforced end to end).** The workflow performs **no
consequential action**: it assigns no department, links no duplicate, resolves,
rejects or closes nothing and writes no `complaint_status_history` row. It
returns recommendations plus the review requirements; the Phase 4/5 officer
endpoints remain the only paths that act on a complaint. A human-review
interrupt is an **acknowledgement request, not an authorisation** — resuming
validates the reviewer and records only that.

**Latency (CPU-only, `llama3.1:latest`).** Deterministic path (`AGENT_USE_LLM=false`):
whole workflow **~6 ms**. Live path: the LLM nodes dominate — e.g. one live run
`intake` ≈ 54 s, `classification` ≈ 34 s, end-to-end **≈ 89 s**; per-node and
total latencies are returned in every result. Expensive LLM nodes are **not**
parallelised (they share one local model).

#### Phase 6 endpoints

| Method | Path | Returns |
|---|---|---|
| `POST` | `/api/v1/workflow/run` | `WorkflowRunResult` — every validated agent output, potential duplicate matches, routing + resolution recommendations, review requirements, outcome and any degraded-mode details |
| `POST` | `/api/v1/workflow/{workflow_run_id}/resume` | `WorkflowRunResult` — acknowledges a paused run (officer/admin only; a citizen is `403`) |

```powershell
# Run the whole workflow for one complaint (optionally persist it once)
curl -X POST http://127.0.0.1:8000/api/v1/workflow/run `
  -H "Content-Type: application/json" `
  -d '{"text":"Garbage has not been collected on our street for five days.",
       "latitude":13.0850,"longitude":80.2100,"persist":true}'

# Acknowledge a run that paused at the review checkpoint (officer/admin only)
curl -X POST http://127.0.0.1:8000/api/v1/workflow/<workflow-run-id>/resume `
  -H "Content-Type: application/json" `
  -d '{"reviewer_id":"<officer-or-admin-uuid>","note":"reviewed"}'
```

**Persistence.** When `persist=true`, the complaint + its agent-execution logs
are written **once, after the run completes** (never on an interrupt). A replayed
run with the same `request_id` is idempotent: the existing complaint is reused,
so a retry cannot create a second complaint or a second set of log rows. No new
migration was needed — every column already existed.

---

## 3. Verified environment

| Component | Value |
|---|---|
| OS | Windows |
| Python | 3.11.9 |
| PostgreSQL | 17.6 @ `localhost:5432` |
| Ollama | 0.40.17 @ `http://localhost:11434` |
| LLM | `llama3.1:latest` (8B, Q4_K_M, tool-calling capable) |
| Database extensions | `pg_trgm`, `uuid-ossp` |

### Decision: LLM choice

The original plan targeted `qwen2.5:3b`. The local machine already has
`llama3.1:latest` pulled, so CivicBridge AI uses **`llama3.1:latest`** and no
extra download is needed. Qwen can be benchmarked later by changing one line:

```dotenv
OLLAMA_MODEL=qwen2.5:3b
```

### Decision: PostgreSQL extensions

`pgvector` and `PostGIS` are **not installed** on this machine and Docker is
unavailable, so Phase 1 uses **stock PostgreSQL 17** plus two extensions that
ship with it:

* `pg_trgm` — trigram fuzzy matching, used as a fast pre-filter for duplicate detection
* `uuid-ossp` — UUID generation (PostgreSQL 17 also has native `gen_random_uuid()`)

Consequences, applied from Phase 4 onward:

* **Vector search** — embeddings are stored in PostgreSQL and cosine similarity
  is computed in Python with `numpy` (fine at prototype scale). Switching to
  `pgvector` later only changes the query layer, not the agent interface.
* **Geospatial** — latitude/longitude columns with haversine distance in SQL
  instead of PostGIS `GEOGRAPHY` types.

`docker-compose.yml` documents the intended production topology
(PostgreSQL + pgvector + PostGIS) behind an opt-in compose profile.

### Decision: CPU-only inference

This host's CUDA runtime cannot initialize the Ollama `llama-server` backend:

```
llama-server process no longer running: exit status 0xc0000409
CUDA error: shared object initialization failed
```

GPU-backed requests therefore return HTTP 500. CivicBridge AI sets
`OLLAMA_NUM_GPU=0` so inference runs on CPU and works reliably (verified).
Once the GPU driver/runtime is repaired, set `OLLAMA_NUM_GPU` to a layer count
(or remove it) to re-enable acceleration — no code change required.

### Decision: LangChain integration version

`langchain-ollama` 1.1+ calls `BaseChatModel._add_version()`, which does not
exist in the `langchain-core` 1.2.x already installed on this machine, and
raises `AttributeError` at model construction. `langchain-ollama==1.0.0` is the
newest release compatible with that core version and is pinned in
`backend/requirements.txt`. Upgrading both packages together later will lift
this pin.

---

## 4. Repository layout

```
CivicBridge-AI/
├── frontend/                     # React + Vite + Tailwind (Phase 8/9)
├── backend/
│   ├── app/
│   │   ├── agents/               # Phases 3-6 - intake, classification, duplicate, evidence, routing, resolution, pipeline, persistence + workflow (LangGraph)
│   │   ├── api/                  # FastAPI routers (agents.py, duplicates.py, evidence.py, routing.py, workflow.py)
│   │   ├── core/
│   │   │   ├── config.py         # Pydantic Settings from environment
│   │   │   └── logging.py        # Central logging setup
│   │   ├── database/
│   │   │   └── session.py        # Engine, session factory, connectivity probe
│   │   ├── datasets/             # Phase 2 - generator, reference data, seeding, report
│   │   ├── models/               # Phase 2 - SQLAlchemy models for the 10 tables
│   │   ├── schemas/              # Pydantic schemas (agent results in Phases 3-5, workflow state in Phase 6)
│   │   ├── services/
│   │   │   └── ollama_service.py # Ollama health, model verification, LLM calls
│   │   ├── cli.py                # health | generate-dataset | seed-db | dataset-report
│   │   └── main.py               # FastAPI entrypoint
│   ├── alembic/                  # Phase 2 - migration environment + versions
│   ├── scripts/init_db.sql       # Database + role bootstrap
│   ├── pyproject.toml            # pytest + ruff configuration
│   └── requirements.txt
├── agents/
│   ├── ollama_test.py            # Standalone LLM connectivity script
│   ├── intake_agent.py           # Phase 3 - runnable Intake Agent
│   └── classification_agent.py   # Phase 3 - runnable Classification Agent
├── ml/
│   ├── classification/           # Reserved
│   ├── duplicate_detection/      # Phase 4 - offline benchmark (evaluate.py + labelled_pairs.json + report)
│   └── priority_prediction/      # Reserved
├── datasets/                     # Phase 2 - synthetic complaints + dataset report
├── tests/
│   ├── test_phase1_health.py
│   ├── test_phase2_database.py
│   ├── test_phase2_dataset.py
│   ├── test_intake_agent.py
│   ├── test_classification_agent.py
│   ├── test_phase3_integration.py
│   ├── test_phase4_duplicate_agent.py
│   ├── test_phase4_api_integration.py
│   ├── test_phase5_routing_agent.py
│   ├── test_phase5_api_integration.py
│   └── test_phase6_workflow.py
├── docs/
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## 5. Setup

### 5.1 Prerequisites

* Python 3.11+
* PostgreSQL 17 running on `localhost:5432`
* Ollama running on `localhost:11434` with the configured model pulled

### 5.2 Install Python dependencies

```powershell
cd backend
python -m pip install -r requirements.txt
```

### 5.3 Create the database and application role

Run once, as a PostgreSQL superuser. The script is idempotent.

```powershell
$env:PGPASSWORD="<postgres-superuser-password>"
& "C:\Program Files\PostgreSQL\17\bin\psql.exe" -U postgres -h localhost -p 5432 `
  -f "backend/scripts/init_db.sql"
```

This creates:

* role `civicbridge_app` — `NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS`, login-only
* database `civicbridge`, owned by `civicbridge_app`
* `pg_trgm` and `uuid-ossp` extensions
* `public` schema write access revoked from `PUBLIC`

Verify as the application role:

```powershell
$env:PGPASSWORD="civicbridge_dev_password"
& "C:\Program Files\PostgreSQL\17\bin\psql.exe" -U civicbridge_app -h localhost -d civicbridge -c "select current_user, current_database();"
```

### 5.4 Configure the environment

```powershell
Copy-Item .env.example .env
```

Edit `.env` — importantly:

```dotenv
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1:latest
```

`.env` is git-ignored. The default development password is a throwaway local
credential; replace it before any shared or deployed environment.

### 5.5 Pull the model (only if missing)

```powershell
ollama list                        # check what is already available
ollama pull llama3.1:latest
```

---

## 6. Running verification

### 6.1 Full environment verification (primary command)

```powershell
cd backend
python -m app.cli health
```

Expected shape of the output:

```
==============================================================================
 CivicBridge AI - Environment Verification
 environment=development  version=0.1.0
==============================================================================

  config: model=llama3.1:latest  base_url=http://localhost:11434
  config: database=postgresql+psycopg2://civicbridge_app:***@localhost:5432/civicbridge

  [PASS] PostgreSQL connection     server 17.6 @ localhost:5432
  [PASS] CivicBridge database      civicbridge (owner civicbridge_app, user civicbridge_app)
  [PASS] Database extensions       extensions: pg_trgm, plpgsql, uuid-ossp
  [PASS] Ollama service            v0.40.17 @ http://localhost:11434 (2 model(s) pulled)
  [PASS] Model llama3.1:latest     8.0B Q4_K_M 4.9 GB | capabilities: completion, tools
  [PASS] LangChain Ollama          provider: langchain-ollama
  [PASS] LLM response test         <latency> ms via langchain-ollama (llama3.1:latest)

-----------------------------------------------------------------------------
  7 passed | 0 failed | 0 skipped
-----------------------------------------------------------------------------
```

Useful flags:

```powershell
python -m app.cli health --no-llm     # infrastructure only, skips inference
python -m app.cli health --json       # machine-readable results
python -m app.cli health --verbose    # full detail for every check
```

Exit code is `0` when everything passes, `1` otherwise.

### 6.2 Database commands (Phase 2)

```powershell
cd backend
python -m app.cli generate-dataset --count 1050   # writes datasets/*.json|csv + report
python -m app.cli seed-db                         # seeds departments + guidelines
python -m app.cli dataset-report                  # validates the generated dataset
```

### 6.3 Standalone agent-side LLM check

```powershell
python agents/ollama_test.py
```

Sends one real grievance-classification prompt to `llama3.1:latest` and prints
the model's live response.

### 6.4 Running the agents (Phase 3)

The agents are importable from `backend/app/agents/`, and two thin wrappers make
them runnable without the API:

```powershell
python agents/intake_agent.py "Garbage has not been collected near our street for five days."
python agents/intake_agent.py --json "குப்பை அள்ளப்படவில்லை"     # Tamil
python agents/intake_agent.py --no-llm "..."                    # deterministic only

python agents/classification_agent.py "For five days garbage has not been collected."
python agents/classification_agent.py --from-json intake.json   # reuse an intake result
```

Each run prints the validated result; `--json` emits the exact Pydantic payload.
The local model is optional — with `--no-llm`, or when Ollama is down, the
deterministic layer produces the same schema with `degraded=true`.

### 6.5 API smoke test

```powershell
cd backend
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

* `GET /` → application metadata
* `GET /health` → PostgreSQL + Ollama + model availability
* `POST /api/agents/intake` · `/classify` · `/analyze-complaint` → the intake + classification agents
* `POST /api/duplicates/analyze` → potential duplicate matches for an inline or stored complaint
* `GET /api/complaints/{complaint_id}/duplicates` → paginated potential matches for a stored complaint
* `POST /api/complaints/{complaint_id}/duplicates/review` → record an officer's approve/reject decision
* `POST /api/evidence/analyze` → OpenCV evidence measurement
* `POST /api/routing/recommend` → department recommendation for an inline complaint
* `GET /api/complaints/{complaint_id}/routing` → department recommendation for a stored complaint
* `POST /api/complaints/{complaint_id}/routing/decision` → record an officer's approve/reject routing decision
* `POST /api/resolution/recommend` → guideline-backed next steps with the unverified SLA disclosure
* `GET /docs` → interactive OpenAPI UI

Example:

```powershell
curl -X POST http://127.0.0.1:8000/api/agents/analyze-complaint `
  -H "Content-Type: application/json" `
  -d '{"text": "For the past five days garbage has not been collected near our street."}'
```

Add `"persist": true` to the same request to write the complaint and its agent
execution logs to PostgreSQL.

### 6.6 Tests

```powershell
cd backend
python -m pytest ../tests -q                        # whole suite
python -m pytest ../tests -q -k "not live"          # skip live Ollama inference
python -m pytest ../tests/test_intake_agent.py -v   # one file
python -m pytest ../tests/test_phase4_api_integration.py -v   # Phase 4 API + review workflow
python -m pytest ../tests/test_phase5_api_integration.py -v   # Phase 5 API + routing-decision workflow
python -m pytest ../tests/test_phase6_workflow.py -v          # Phase 6 orchestration workflow
```

Integration tests skip automatically (`SKIP`) when PostgreSQL or Ollama is
unavailable, so the suite is safe to run anywhere. The live tests make real
`llama3.1:latest` calls and re-use the response cache, so the whole run costs
only a couple of inferences. The Phase 4 API tests cover the duplicate
endpoints, the no-match and multiple-match responses, invalid complaint ids,
missing coordinates, the evidence endpoint (including invalid evidence), the
human-approval workflow, audit logging and the guarantee that detection never
auto-merges. The Phase 5 tests cover the routing and resolution agents directly
(known/unknown categories, missing and ambiguous wards, unmapped departments,
unverified SLAs, missing guidelines, recommendation-only invariants) and the API
workflow (recommendation, approval, rejection, audit history, unauthorized
reviewers, non-assignable statuses, and the guarantee that nothing is assigned,
resolved or closed automatically). The Phase 6 tests run the **whole graph**
with mocked LLMs (successful end-to-end run, missing location, low-confidence
and too-thin / empty complaints, the critical-priority safeguard, potential
duplicates, optional evidence, an unavailable model, malformed output, a failed
node, bounded-retry idempotency, interrupt/resume, the `/api/v1/workflow`
endpoints, persistence + audit integrity and unauthorized resume), plus a small
number of live runs against the real local model.

### 6.7 Duplicate-detection benchmark

```powershell
python ml/duplicate_detection/evaluate.py
```

Runs the offline benchmark against the hand-labelled pairs
(`ml/duplicate_detection/labelled_pairs.json`) and the planted synthetic set,
and writes `report.json` / `report.md`. The current TF-IDF + Haversine
configuration is the validated baseline; a char-only vs word-only vs combined
n-gram comparison is planned as a separate experiment before the vectoriser is
changed.

### 6.8 Lint and formatting

```powershell
cd backend
python -m ruff check app ../tests ../agents
python -m ruff format --check app ../tests ../agents
```

---

## 7. Configuration reference

| Variable | Default | Purpose |
|---|---|---|
| `APP_NAME` | `CivicBridge AI` | Display name |
| `ENVIRONMENT` | `development` | Runtime environment |
| `DEBUG` | `true` | Debug logging |
| `API_HOST` / `API_PORT` | `127.0.0.1` / `8000` | API bind address |
| `SUPPORT_LANGUAGES` | `en,ta` | Enabled complaint languages |
| `POSTGRES_HOST` | `localhost` | PostgreSQL host |
| `POSTGRES_PORT` | `5432` | PostgreSQL port |
| `POSTGRES_USER` | `civicbridge_app` | Application role |
| `POSTGRES_PASSWORD` | `civicbridge_dev_password` | Application role password |
| `POSTGRES_DB` | `civicbridge` | Database name |
| `DATABASE_URL` | _(unset)_ | Full SQLAlchemy URL override |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama endpoint |
| `OLLAMA_MODEL` | `llama3.1:latest` | **Active LLM — swap freely, no code change** |
| `OLLAMA_HEALTH_TIMEOUT` | `5` | Seconds for health probes |
| `OLLAMA_LLM_TIMEOUT` | `600` | Seconds for generation |
| `OLLAMA_NUM_GPU` | `0` | GPU layers to offload; `0` = CPU only, unset = Ollama decides |
| `OLLAMA_TEMPERATURE` | `0` | Sampling temperature |
| `OLLAMA_NUM_PREDICT` | `256` | Max generated tokens |
| `OLLAMA_KEEP_ALIVE` | `5m` | Model residency in memory |
| `AGENT_USE_LLM` | `true` | Run the LLM enrichment layer; `false` = deterministic Python only |
| `AGENT_CONFIDENCE_THRESHOLD` | `0.60` | Below this, the classification agent flags the complaint for human review |
| `AGENT_MAX_REPAIR_ATTEMPTS` | `1` | Extra attempts to obtain schema-valid JSON after the first model response |
| `AGENT_NUM_PREDICT` | `512` | Token budget for agent structured output |
| `AGENT_RESPONSE_CACHE_SIZE` | `256` | Bounded in-process cache of identical prompts; avoids duplicate inference |
| `ORCHESTRATION_ENABLED` | `true` | Enable the `/api/v1/workflow` orchestration endpoints |
| `ORCHESTRATION_NODE_TIMEOUT_SECONDS` | `240` | Wall-clock ceiling for one graph node (a hung call fails, then degrades) |
| `ORCHESTRATION_MAX_NODE_ATTEMPTS` | `2` | Total attempts (first try + retries) for one node; transient errors only |
| `ORCHESTRATION_TOTAL_TIMEOUT_SECONDS` | `900` | Upper bound on the whole run, checked between nodes |
| `ORCHESTRATION_CHECKPOINTING_ENABLED` | `true` | Compile the graph with the in-process checkpointer (enables resume) |
| `ORCHESTRATION_INTERRUPT_FOR_REVIEW` | `false` | Default for whether the human-review checkpoint pauses (opt in per request) |

---

## 8. Design rules the codebase holds to

1. **No fake AI output.** Every LLM response in the product comes from a real inference call.
2. **Fail loudly, degrade gracefully.** Ollama and PostgreSQL outages raise typed
   errors and surface as failed checks — never as invented data.
3. **Humans decide.** No agent may autonomously close a complaint or take an
   irreversible administrative action (assignment and resolution are
   human-gated since Phase 5; extended to the full workflow in Phase 6).
4. **Configuration over code.** Model, endpoint and database are environment-driven.
5. **Deterministic first.** Anything the code can compute without the model — language,
   duration, keywords, routing, SLA — it does, so the result stays correct when the
   model is slow, unavailable or wrong.
6. **A model opinion never decides a consequential outcome on its own.** Priority
   above the evidence, a critical classification, an invented department - each is
   capped, corroborated or routed to a human, and the decision trail is recorded.
7. **Synthetic data only.** No claim of integration with real government systems.

---

## 9. Roadmap

| Phase | Scope |
|---|---|
| 1 | ✅ Project structure, environment, Ollama + PostgreSQL verification |
| 2 | ✅ Database schema (10 tables) + 1,050-complaint synthetic dataset |
| 3 | ✅ Complaint Intake agent + Classification agent |
| 4 | ✅ Duplicate Detection + Evidence Verification agents, their FastAPI endpoints and the officer link-review workflow |
| 5 | ✅ Department Routing + Resolution Recommendation agents, their FastAPI endpoints and the officer assignment-approval workflow |
| 6 | ✅ LangGraph orchestration, checkpointing, human-in-the-loop approval |
| 7 | FastAPI integration (auth, RBAC, complaint + agent endpoints) |
| 8 | React citizen portal |
| 9 | Government officer dashboard |
| 10 | Analytics, GIS, evaluation metrics, Docker deployment |

---

## 10. Risk and limitations register

Tracked items that are knowingly carried forward rather than fixed in place.
None of them blocks the current phase; they are recorded so they are not lost.

| ID | Item | Type | Status / disposition |
|---|---|---|---|
| R-01 | A Tamil-transliteration pair is a known **false negative** in duplicate detection. | Known limitation | Documented; the threshold is deliberately **not** lowered to force one example through. More transliterated pairs are being collected to expand the benchmark. |
| R-02 | The **22 hand-labelled duplicate pairs** are a preliminary benchmark, not a production accuracy claim. | Known limitation | Labelled "preliminary" wherever it is quoted; the held-out set must be expanded before any accuracy figure is treated as real. |
| R-03 | The `char_wb` TF-IDF vectoriser is the validated baseline; **char-only / word-only / combined** n-grams have not yet been compared. | Open experiment | Scheduled as a separate experiment; a combined vectoriser is adopted only if precision/recall/F1 measurably improve (especially for Tamil / transliterated Tamil). |
| R-04 | The **0.85 cross-category penalty** is an initial *configurable* safeguard, not a settled threshold. | Open validation | To be validated against more labelled examples before it is treated as final. |
| R-05 | Two FastAPI `on_event` **deprecation warnings** (`app/main.py`). | Tech debt | Non-blocking; migrate to lifespan event handlers. |
| R-06 | All departments, service guidelines and SLA values are **synthetic prototype data** (`is_verified=False`). | Accepted (by design) | Labelled prototype everywhere; never presented as an official municipal assignment or government commitment. |
| R-07 | The Phase 6 checkpointer is **in-process and not durable across restarts** (`langgraph-checkpoint` ships no SQLite saver here). | Known limitation | Fine for the synchronous `/run` call and for resume within one process; a paused run is lost if the server restarts. A persistent checkpointer (e.g. Postgres) is required before interruptions are used in production. |
| R-08 | Live LLM latency is high on CPU-only inference (one workflow run measured ≈ 89 s end to end). | Known limitation | Acceptable for a prototype; per-node timings are recorded so a bottleneck is visible. LLM nodes are deliberately **not** parallelised against the single local model. |

**Phase 3 safeguard — confirmed implemented.** The classification agent caps a
model-driven escalation at `HIGH` (`MODEL_ESCALATION_CEILING`); a `CRITICAL`
priority requires corroborating severity evidence in the complaint itself, and
otherwise routes to a human. Neither the model's opinion nor its confidence can
decide a critical priority on its own
(`app/agents/classification.py`).

**Phase 6 safeguard — verified through orchestration.** The same cap holds when
the classification agent runs inside the LangGraph workflow: a model-only
`CRITICAL` claim is applied at most as `HIGH`, flagged for human review, and the
workflow never assigns, merges, resolves, rejects or closes anything on its own
(`tests/test_phase6_workflow.py`).

---

## 11. Troubleshooting

| Symptom | Fix |
|---|---|
| `Ollama is not reachable` | Start with `ollama serve`; confirm `OLLAMA_BASE_URL` |
| `Model 'x' is not pulled` | `ollama pull x`, or set `OLLAMA_MODEL` to a model from `ollama list` |
| `ModuleNotFoundError: langchain_ollama` | `python -m pip install langchain-ollama ollama` |
| `psycopg2.OperationalError` | Confirm the PostgreSQL service is running and the credentials in `.env` match |
| `password authentication failed for user "civicbridge_app"` | Re-run `backend/scripts/init_db.sql` |
| `database "civicbridge" does not exist` | Re-run `backend/scripts/init_db.sql` |
| `CUDA error: shared object initialization failed` / HTTP 500 from Ollama | Set `OLLAMA_NUM_GPU=0` (CPU inference) or repair the GPU driver |
| `AttributeError: 'ChatOllama' object has no attribute '_add_version'` | `python -m pip install langchain-ollama==1.0.0` (see the version note above) |
| First inference is slow | One-time model load into RAM; later calls reuse it (`OLLAMA_KEEP_ALIVE`) |
| CPU inference feels slow for long answers | Lower `OLLAMA_NUM_PREDICT`, or switch `OLLAMA_MODEL` to a smaller model |
