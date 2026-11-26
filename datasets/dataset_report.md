# CivicBridge AI - Phase 2 Dataset Report

Generated: `2026-11-25T14:08:49.074009+00:00`

> Every number below was measured against the live PostgreSQL database.
> The dataset is entirely synthetic; nothing here is real citizen data.

## Database

- Database: `civicbridge` on localhost:5432
- Role: `civicbridge_app`
- Alembic revision: `0255b8fcf1e2`

## Row counts per table

| Table | Rows |
| --- | ---: |
| users | 34 |
| departments | 4 |
| officers | 8 |
| complaints | 1050 |
| complaint_evidence | 0 |
| complaint_assignments | 0 |
| complaint_status_history | 1860 |
| agent_execution_logs | 0 |
| escalation_records | 0 |
| service_guidelines | 4 |

## Complaints (total: 1050)

### Category distribution

| Value | Count | Share |
| --- | ---: | ---: |
| waste_management | 317 | 30.2% |
| road_maintenance | 266 | 25.3% |
| water_supply | 260 | 24.8% |
| street_lighting | 207 | 19.7% |

### Language distribution

| Value | Count | Share |
| --- | ---: | ---: |
| en | 627 | 59.7% |
| ta | 261 | 24.9% |
| ta-latn | 162 | 15.4% |

### Priority distribution

| Value | Count | Share |
| --- | ---: | ---: |
| medium | 423 | 40.3% |
| low | 265 | 25.2% |
| high | 262 | 25.0% |
| critical | 100 | 9.5% |

### Status distribution

| Value | Count | Share |
| --- | ---: | ---: |
| submitted | 240 | 22.9% |
| under_review | 180 | 17.1% |
| assigned | 150 | 14.3% |
| in_progress | 130 | 12.4% |
| resolved | 110 | 10.5% |
| awaiting_clarification | 50 | 4.8% |
| closed | 50 | 4.8% |
| escalated | 50 | 4.8% |
| duplicate | 50 | 4.8% |
| rejected | 40 | 3.8% |

## Planted duplicates

- Total duplicates: 50
- Similarity range: 0.822 - 0.987

| Duplicate | Original | Similarity |
| --- | --- | ---: |
| CB-2026-001001 | CB-2026-000179 | 0.869 |
| CB-2026-001002 | CB-2026-000964 | 0.951 |
| CB-2026-001003 | CB-2026-000463 | 0.956 |
| CB-2026-001004 | CB-2026-000358 | 0.929 |
| CB-2026-001005 | CB-2026-000413 | 0.872 |

## Geolocation coverage

- With coordinates: 979 (93.24%)
- Missing coordinates (NULL pair): 71

## Prototype reference data

- Departments: 4
- Service guidelines: 4

> Prototype directory authored for CivicBridge AI. Illustrative municipal units only - not a verified real government assignment.

> Prototype assumption for CivicBridge AI. SLA durations are configurable placeholders - not an officially published service rule.

## Validation results

| Check | Result | Detail |
| --- | --- | --- |
| All 10 tables exist in PostgreSQL | PASS | 10/10 present |
| Alembic migration applied | PASS | revision=0255b8fcf1e2 |
| Dataset size >= 1000 complaints | PASS | complaints=1050 |
| All 4 categories present | PASS | categories=road_maintenance, street_lighting, waste_management, water_supply |
| All 3 languages present (en, ta, ta-latn) | PASS | languages=en, ta, ta-latn |
| Planted duplicates present and resolvable | PASS | duplicates=50 orphaned=0 self_referential=0 |
| Missing coordinates handled (both columns NULL together) | PASS | without_location=71 half_pairs=0 |
| No invalid coordinates stored | PASS | out_of_range=0 |
| Status values are all controlled vocabulary | PASS | distinct_statuses=10 |
| Priority values are all controlled vocabulary | PASS | distinct_priorities=4 |
| Foreign key relationships resolve | PASS | checked=5 relationships | violations=0 |
| Every department is labelled prototype data | PASS | prototype=4/4 |
| No service guideline claims official verification | PASS | unverified=4/4 |

**13/13 checks passed.**
