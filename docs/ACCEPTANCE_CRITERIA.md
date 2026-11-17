# Acceptance Criteria — Phase 0 contract

Status key: ✅ implemented+tested · 🟡 implemented, untested · ❌ gap.

## Ontology & schema

- AC1 Label `key` is a slug, unique per project; duplicate → 409. ✅
- AC2 Label `color` must be `#rrggbb`; `key`/`name` length caps enforced. ✅
- AC3 Attributes ≤20 unique identifier keys; duplicates rejected. ✅
- AC4 Creating a label bumps `project.schema_version`. ✅
- AC5 Attribute *values* honor `options` when declared (else free text). ✅

## Upload pipeline

- AC6 Accept PNG/JPEG/WebP/BMP/GIF/TIFF ≤20 MB ≤40 MP (re-encoded to
  JPEG — BMP/GIF/TIFF converted server-side); ≤30 per request (client
  chunks larger selections — see AC50); else 400. ✅
- AC7 Non-image / corrupt payloads rejected (Pillow verify + load). ✅
- AC8 Identical bytes (SHA-256) can't upload twice per project → 400. ✅
- AC9 Stored asset is re-encoded JPEG; original never exposed. 🟡
- AC10 Assets served only to workspace members, `no-store` + `nosniff`. ✅

## Annotation document

- AC11 Coordinates finite numbers in [0,1]; NaN/out-of-range/bool → 400. ✅
- AC12 `box` positive area; `polygon` 3–500 pts; `point` exactly 1. ✅
- AC13 Region ids unique, slug ≤80; unknown/archived label → 400. ✅
- AC14 Attribute keys ⊆ label's declared keys; values strings ≤1000. ✅
- AC15 Relations reference existing distinct regions; key is slug ≤64. ✅
- AC16 >1000 regions / >2000 relations → 400. 🟡 (limit enforced, untested)
- AC17 Every region has label/type/points; extra keys rejected. ✅

## Workflow & concurrency

- AC18 Full cycle: upload → assign → save → submit → approve → release. ✅
- AC19 Stale revision → 409, existing data untouched. ✅
- AC20 Assignee can't review own task → 403. ✅
- AC21 `changes_requested` without note → 400. ✅
- AC22 `approved` task can't be edited or reassigned → 403/400. ✅
- AC23 Review/resubmit round-trip works (changes → re-save → submit). ✅

## Roles & isolation

- AC24 Annotator can't create labels, upload, assign, review, release. ✅
- AC25 Reviewer+owner can upload/assign/review/release. 🟡
- AC26 Cross-workspace access → **404** on page, asset, task, release,
  and POST endpoints. ✅
- AC27 All mutations require login + POST + CSRF token. ✅
- AC28 Member add creates account w/ validated password; weak → 400. ✅
- AC29 Pages render for members: home, project, editor. ✅

## Releases & export

- AC30 Release requires ≥1 approved task; else 400. ✅
- AC31 Snapshot contains labels, approved tasks+annotations, provenance. ✅
- AC32 Snapshot immutable: label rename post-release doesn't alter it. ✅
- AC33 Download = `release-<uuid>.json`, members only. ✅
- AC34 COCO export available alongside v1 JSON (`/releases/<id>/coco/`):
  box→bbox, polygon→segmentation, point→keypoints, labels→categories,
  relations→extension field, pixel coords denormalized. ✅

## Editor UX

- AC35 Autosave every 5 s when dirty — browser-verified. ✅
- AC36 localStorage crash recovery restores unsaved work after reload —
  browser-verified. ✅
- AC37 Revision conflict ⇒ 409, save blocked, work kept locally —
  browser-verified. ✅
- AC38 Draw regions, undo/redo, attributes, relations, save→reload
  persistence, submit lock — Playwright e2e. ✅
  (box resize handles, spacebar pan, zoom untested)
- AC39 `beforeunload` warning while dirty. 🟡

## Non-functional

- AC40 Audit `Event` written for every mutation w/ actor+payload. 🟡
- AC41 SQLite default, Postgres via env vars — no code change. 🟡
- AC42 DEBUG=0 ⇒ secret-key required, secure cookies, SSL redirect, HSTS. 🟡
- AC43 Queue paginated at 50/page, filters preserved across pages. ✅
- AC44 Submit shows validation warnings (empty regions/attributes)
  before confirming. 🟡
- AC45 One geometry type per project: first saved annotation locks
  `project.geometry`; mismatched or mixed saves → 400; a rejected save
  does not move the lock. ✅
- AC46 Editor hides incompatible tools (+ shortcuts) once the geometry
  is known — locked project, or first region drawn on an undecided one —
  and re-enables them if all regions are deleted. Polygon/point buttons
  hidden after a box is drawn: e2e-verified. ✅
- AC47 Owner can set/clear the geometry lock from the project page;
  non-owner → 400, invalid value → 400. ✅
- AC48 `GET /projects/<id>/export/` streams a zip (annotations.json in
  `annotation-studio/v1` + `images/`) for every task that has regions —
  works after a single labeled image, no release needed. Owner/reviewer
  only; annotator → 403, zero labeled → 400. ✅
- AC49 After drawing a region the editor switches to Select so it is
  immediately draggable/resizable — e2e-verified (drag moves the box). ✅
- AC50 Upload form accepts any number of files: the browser posts them
  in sequential batches of 30 with a live counter, falls back to
  per-file retries on a failed batch, and reloads the queue when done —
  e2e-verified with a 35-file selection. ✅
- AC51 Review decisions are atomic (`revision` + `state='submitted'`
  guard): a second decision on the same task → 409, first one wins. ✅
- AC52 Any member can comment on a task (≤2000 chars); comments render
  in the task history for all members. ✅
- AC53 Any saved/submitted/restored revision can be restored on an
  editable task — re-validated against current schema and geometry lock;
  stale revision → 409; non-editable actor → 403. ✅
- AC54 `review-next` serves the oldest submitted task; recording a
  review auto-advances to the next one or the project page. `spot-check`
  opens a random approved task and audits `quality.sampled`. ✅
- AC55 Project progress card: per-member state counts, review
  acceptance rate, reviews given per reviewer, 7-day submission count —
  owner/reviewer only. ✅
- AC56 Label archive/restore toggles usability, bumps `schema_version`,
  and makes future saves using it fail validation. ✅
- AC57 YOLO zip export: `data.yaml` + `labels/<stem>.txt` + `images/`,
  detection rows for boxes, segmentation for polygons, tiny boxes for
  points; stems paired. ✅
- AC58 Bearer API tokens scope reads to their workspace: wrong-scope →
  404, bad/revoked → 401, `last_used_at` tracked. ✅
- AC59 Webhooks fire post-commit with `X-Godezk-Signature` HMAC-SHA256;
  success and failure both logged in `WebhookDelivery`; ping endpoint
  verifies connectivity. ✅
- AC60 `AWS_STORAGE_BUCKET_NAME` switches private uploads to S3 via
  django-storages; filesystem stays default. 🟡 (config, untested w/o S3)
- AC61 `POST /projects/<id>/distribute/` splits tasks round-robin across
  chosen annotators (non-annotator roles rejected — 400) — `unassigned`
  (default) or `all` editable scope; `shuffle` randomizes task order
  before splitting; submitted/approved always excluded; atomic bulk
  update + audit. ✅

## Phase 5 — production hardening

- AC62 Job queue (`studio_job`): `manage.py runworker` polls `run_pending`;
  atomic claim, exponential backoff retry, `max_attempts` (default 3),
  then `dead` dead-letter — nothing silently lost. ✅
- AC63 Worker crash recovery: jobs stuck `running` > 10 min are reclaimed
  to `pending` on the next `run_pending` pass. ✅
- AC64 Failed webhook deliveries enqueue `webhook.deliver` jobs and are
  retried by the worker; `WebhookDelivery` records outcome. ✅
- AC65 `manage.py backupdata` writes a `.tar.gz` of the SQL dump + media;
  `manage.py restoredata` rebuilds DB + files (verified roundtrip). ✅
- AC66 `GET /health/`: anon gets `{status}` only; superuser gets DB ping,
  project/task counts, `jobs_pending`, `jobs_dead`, `deliveries_failed`
  — the alert surface for queue growth, worker and export failures. ✅
- AC67 DB indexes: `Task(project,state)`, `Task(project,assignee)`,
  `Event(project,created_at)`, `Event(project,action)`,
  `Job(status,run_after)` for queue/review/audit hot paths. ✅
- AC68 SQLite concurrency: WAL mode + busy timeout + request-level retry
  on `database is locked`; 8 racing reviewers → exactly one decision,
  losers get clean 409 — verified by real-thread test on file-backed
  test DB. ✅
- AC69 `manage.py loadtest`: bulk-creates tasks then fires N-way
  concurrent saves through the revision-guard update; reports
  throughput and p50/p95/p99 latency plus queue/review query times. ✅

## Gap backlog

| ID | Gap | Status |
|---|---|---|
| G1 | Attribute `options` | ✅ Phase 1 |
| G2 | Task queue pagination / bulk ops | ✅ Phase 2 (pagination; bulk ops open) |
| G3 | COCO export | ✅ Phase 2 |
| G4 | Browser tests for autosave, recovery, conflict | ✅ Phase 2 (beforeunload manual) |
