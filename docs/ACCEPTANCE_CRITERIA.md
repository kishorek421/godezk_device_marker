# Acceptance Criteria — Phase 0 contract

Status key: ✅ implemented+tested · 🟡 implemented, untested · ❌ gap.

## Ontology & schema

- AC1 Label `key` is a slug, unique per project; duplicate → 409. ✅
- AC2 Label `color` must be `#rrggbb`; `key`/`name` length caps enforced. ✅
- AC3 Attributes ≤20 unique identifier keys; duplicates rejected. ✅
- AC4 Creating a label bumps `project.schema_version`. ✅
- **AC5 (G1)** Attribute *values* honor `options` when declared. ❌ Phase 1

## Upload pipeline

- AC6 Accept PNG/JPEG/WebP ≤20 MB ≤40 MP; ≤30 per batch; else 400. ✅
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
- **AC34 (G3)** COCO export available alongside v1 JSON. ❌ Phase 2

## Editor UX

- AC35 Autosave every 5 s when dirty; Ctrl+S; status line reflects state. 🟡
- AC36 localStorage crash recovery offered when revision matches. 🟡
- AC37 Conflict ⇒ saving blocked until reload; work kept locally. 🟡
- AC38 Draw box/polygon/point, undo/redo, attributes, relations,
  save→reload persistence, submit lock — Playwright e2e
  (`test_browser.py`). ✅ (drag-move/zoom/hotkeys untested)
- AC39 `beforeunload` warning while dirty. 🟡

## Non-functional

- AC40 Audit `Event` written for every mutation w/ actor+payload. 🟡
- AC41 SQLite default, Postgres via env vars — no code change. 🟡
- AC42 DEBUG=0 ⇒ secret-key required, secure cookies, SSL redirect, HSTS. 🟡
- **AC43 (G2)** Queue pagination beyond 200 items. ❌ Phase 2

## Phase 0 gaps → backlog

| ID | Gap | Target |
|---|---|---|
| G1 | Attribute `options` (enumerated values) in label schema + validation | Phase 1 |
| G2 | Task queue pagination / bulk ops | Phase 2 |
| G3 | COCO export | Phase 2 |
| G4 | Browser tests for autosave, recovery, conflict path, beforeunload (AC35–37, 39) | Phase 2 |
