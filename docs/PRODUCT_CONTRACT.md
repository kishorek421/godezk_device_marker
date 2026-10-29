# Product Contract — Godezk Marker

Phase 0 deliverable. This document freezes what the platform is.
Companion artifacts:

| Artifact | File |
|---|---|
| Annotation schema (formal) | [ANNOTATION_SCHEMA.md](ANNOTATION_SCHEMA.md) |
| User roles + workflow | [WORKFLOW.md](WORKFLOW.md) |
| Database model | [DATA_MODEL.md](DATA_MODEL.md) |
| Editor wireframes | [WIREFRAMES.md](WIREFRAMES.md) |
| Acceptance criteria | [ACCEPTANCE_CRITERIA.md](ACCEPTANCE_CRITERIA.md) |

## Product requirements (one paragraph)

Godezk Marker is a **self-hosted image annotation platform** for small teams
building ML datasets. A workspace owner creates projects, defines a label
ontology, and invites annotators and reviewers. Annotators draw regions
(boxes, polygons, points) and typed relations on assigned images; a
different reviewer approves or requests changes; approved work is frozen
into immutable, downloadable JSON releases. Everything is auditable and
nothing is publicly accessible.

---

## 1.1 Annotation modality

**Decision: raster image annotation only for MVP.**

```
Phase 1 (current):                Later:
    Image                            Video (frame tracking)
        ├── Bounding box             Text (NER)
        ├── Polygon                  Document
        ├── Point                    Audio
        ├── Typed relations          3D
        └── Per-region attributes    Multimodal
```

Non-goals for MVP: pixel masks/segmentation, keypoints/skeletons,
image-level classification tags, video, text, audio.

Classification is intentionally excluded — a class tag can be modeled as
a point region with attributes until a dedicated modality ships.

## 1.2 Label ontology

A **Label** is an annotation class scoped to a project.

| Field | Rule |
|---|---|
| `key` | Slug `[-a-zA-Z0-9_]{1,64}`, unique per project. Stable export identifier — never reused. |
| `name` | Display name, ≤80 chars. Free to change. |
| `color` | `#rrggbb` hex, UI only. |
| `attributes` | Ordered list of attribute definitions, ≤20, unique keys. |
| `archived` | Hidden from the editor; existing annotations keep referencing it. |

Attribute definition (contract):

```json
{ "key": "state", "options": ["on", "off"] }
```

- `key` — identifier `[a-zA-Z_][a-zA-Z0-9_]{0,39}`
- `options` — optional list of allowed string values; absent/empty = free
  text ≤1000 chars.

> **Implementation gap:** storage currently holds attribute keys only and
> values are free-form. Adding `options` enforcement is Phase 1 work —
> see ACCEPTANCE_CRITERIA.md G1.

Adding a label increments `project.schema_version`. Annotations record the
schema version they were saved against in the audit event.

## 1.3 Relationships

**Decision: free-form labeled directed edges.** `from → key → to`.

- `key` is a slug `[-a-zA-Z0-9_]{1,64}` — no fixed vocabulary.
- Both endpoints must be regions in the same annotation, `from ≠ to`.
- No per-label-pair constraints, no cardinality rules, no cycles check.
  Semantics are the project owner's convention.

Rationale: keeps the editor simple; a rules engine can be layered on later
without changing the document format.

## 1.4 Users and roles

**Decision: three roles on a workspace, plus implicit "manager" duties
folded into reviewer.**

```
Workspace
    └── Membership (user + role)
            ├── owner     → Admin: everything
            ├── reviewer  → Reviewer + Manager: upload, assign, review,
            │               release, see progress/events
            └── annotator → annotate assigned tasks only
```

| Capability | owner | reviewer | annotator |
|---|---|---|---|
| Create workspace/project | ✓ | | |
| Define labels | ✓ | | |
| Add members / create accounts | ✓ | | |
| Upload images | ✓ | ✓ | |
| Assign tasks | ✓ | ✓ | |
| Edit own assigned task | ✓ | ✓ (if assigned) | ✓ |
| Edit any task | ✓ | | |
| Review submitted tasks | ✓ | ✓ | |
| Create/download releases | ✓ | ✓ | download |

Hard rule: **no self-review** — a reviewer cannot approve their own work.
No self-registration; owners create accounts. SSO is out of scope for MVP.

## 1.5 Dataset scale

**Decision: small-to-medium. Design point ≈ 10⁴ tasks per project,
≤10³ images per upload session.**

Enforced limits:

- Image: PNG / JPEG / WebP, ≤20 MB, ≤40 MP, ≤30 files per batch
- Annotation: ≤1000 regions, ≤2000 relations per image
- Task queue UI: 200 rows (hard cap — see gap G2)

Architecture follows from this: synchronous upload with Pillow
re-encoding (JPEG q95), SHA-256 dedupe per project, local filesystem
media storage, one DB. No queue/workers/object storage needed at this
scale. Revisit if the ceiling moves above ~10⁵ tasks or multi-GB assets.

> **Gap G2:** pagination beyond 200 rows and bulk operations are Phase 2.

## 1.6 Hosting and security

**Decision: self-hosted, single deployable unit. On-prem or private VM.**

- SQLite for dev/small installs; Postgres via `POSTGRES_*` env vars for
  production.
- Single-tenant-per-workspace isolation: every object lookup is scoped
  through membership; outsiders get **404** (existence not leaked).
- Media is private: served through an authenticated view with
  `Cache-Control: private, no-store` and `nosniff`.
- Audit: `Event` table records every mutation with actor, action,
  payload, timestamp.
- Sessions: HttpOnly + SameSite=Lax cookies; secure cookies, SSL redirect
  and HSTS when `DEBUG=0`.
- Assumed sensitive by default (faces, devices, documents): no public
  URLs, no CDN, no third-party calls.

Out of scope for MVP: SSO/OIDC, encryption at rest (defer to infra),
data-retention policies, backups (ops concern), rate limiting.

## 1.7 Export formats

**Decision: first-party JSON release format `annotation-studio/v1`
(now); COCO as the first additional format (Phase 2).**

- A release is an immutable snapshot stored in the DB: labels, approved
  task annotations, provenance events. Editing labels afterward does not
  mutate past releases.
- Coordinates are stored normalized [0,1]; task `width`/`height` are in
  the snapshot so consumers can denormalize.
- COCO export maps: regions→annotations (box→bbox, polygon→segmentation,
  point→keypoints), labels→categories, tasks→images, relations→
  `relations` extension field.

YOLO/VOC deferred — COCO covers the mainstream CV use case and
downstream converters exist.

---

## Frozen vs. open

Everything above is **decided**. The remaining items are explicitly
deferred, not open:

- Relation semantics/rules engine → Phase 2+
- Enumerated attribute options enforcement → Phase 1 (G1)
- Queue pagination/bulk ops → Phase 2 (G2)
- COCO export → Phase 2 (G3)
- SSO → not planned
