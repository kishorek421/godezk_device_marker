# Workflow — roles, task lifecycle, API surface

## Task state machine

```
                assign                    annotate/save
   ┌──────┐   ─────────►   ┌─────────────┐ ─────────► (stays)
   │ open │                │ in_progress │
   └──────┘   ◄─────────   └─────────────┘
      ▲          reject?         │ submit
      │                          ▼
      │                    ┌───────────┐   approve    ┌──────────┐
      │                    │ submitted │ ───────────► │ approved │  (terminal,
      │                    └───────────┘              └──────────┘   exportable)
      │                          │
      │                changes_requested (+ required note)
      │                          ▼
      │               ┌─────────────────────┐
      └────────────── │ changes_requested   │ ── re-save/re-submit by assignee
                      └─────────────────────┘
```

Rules enforced server-side:

- `open / in_progress / changes_requested` are editable — by the assignee
  or an owner.
- `submitted` is locked; only a reviewer decision moves it.
- `submitted` and `approved` tasks cannot be reassigned.
- A reviewer may not decide a task they annotated (`assignee ≠ reviewer`).
- `changes_requested` requires a non-empty note (≤4000 chars).
- `approved` is terminal — no further edits (409/403).

## Geometry lock

Each project annotates in exactly **one geometry type** — the whole
project follows a single annotation flow:

- `project.geometry` starts as `any` (undecided). The first saved
  annotation locks it to that region type; every later save must match
  or the server returns 400. A mixed-type annotation is always rejected,
  and a rejected save leaves the lock untouched (atomic rollback).
- The editor enforces the same rule client-side: once the lock is known —
  or the first region is drawn on an undecided project — incompatible
  tools are hidden and their keyboard shortcuts (`B`/`P`/`K`) are
  inert. Deleting all regions on an undecided project re-opens the
  choice.
- An **owner** can change or clear the lock at any time via the
  "Geometry lock" control on the project page
  (`POST /projects/<id>/geometry/`).

## Concurrency contract

Every mutating call carries the caller's seen `revision`. The server
applies `UPDATE … WHERE id=? AND revision=? AND state=?` atomically and
returns **409** if zero rows matched (someone else saved first). The
editor autosaves every 5 s and blocks further saves after a conflict
until reload. The same guard covers reviews — two reviewers can't both
decide a task; the second submission gets 409.

## Review & quality controls

- **Review queue**: `GET /projects/<id>/review-next/` sends a reviewer
  to the oldest `submitted` task (deterministic order avoids duplicate
  pulls). Recording a decision auto-advances to the next submitted task
  or back to the project page when the queue is empty.
- **Discussion**: any member can comment on a task (`task.comment`,
  ≤2000 chars); comments and review notes appear in the task history.
- **Annotation history**: every save/submit stores the full annotation
  payload with its revision. The history list shows revision, region and
  relation counts; an editable assignee or owner can **restore** any
  prior version (re-validated against the current label schema and
  geometry lock; audited as `annotation.restored`).
- **Quality sampling**: `GET /projects/<id>/spot-check/` opens a random
  `approved` task for re-inspection and logs `quality.sampled`.
- **Progress report** (owner/reviewer on the project page): per-member
  task counts by state, review acceptance rate per assignee, reviews
  given per reviewer, and submissions in the last 7 days.

## Dataset delivery

- **Release formats**: `annotation-studio/v1` JSON, COCO JSON, and
  **YOLO zip** (`data.yaml` + `labels/*.txt` + `images/`; boxes →
  detection rows, polygons → segmentation rows, points → tiny boxes).
- **Label schema lifecycle**: labels can be archived/restored by the
  owner; every change bumps `schema_version`, which each release pins.
- **Large exports**: all zip endpoints stream through disk-spilled
  tempfiles, so release-sized exports don't exhaust memory.
- **Read API** — Bearer tokens (`gm_…`) created per workspace under
  project *Integrations*; scoped to that workspace, revocable:

  ```
  GET /api/v1/projects/                 list workspace projects
  GET /api/v1/projects/<id>/            detail + labels + state counts
  GET /api/v1/projects/<id>/tasks/      task list (?offset=&limit= ≤500)
  GET /api/v1/tasks/<id>/               task + full annotation
  GET /api/v1/releases/<uuid>/          immutable release snapshot
  ```

- **Webhooks** (owner-configured): every audit event POSTs to each
  active hook with `X-Godezk-Event` and `X-Godezk-Signature:
  sha256=<HMAC(secret, body)>`. Deliveries are attempted after commit
  (rolled-back writes never fire), logged in `WebhookDelivery`, and a
  **Ping** button verifies the endpoint. No retries — failed deliveries
  are recorded, not queued.
- **Object storage**: set `AWS_STORAGE_BUCKET_NAME` (+ `AWS_S3_ENDPOINT_URL`
  for S3-compatible hosts) to move private uploads to S3 via
  django-storages; filesystem remains the default.

## Role matrix

| Action | Endpoint | owner | reviewer | annotator |
|---|---|---|---|---|
| Create workspace | `POST /workspaces/create/` | ✓ | ✓ | ✓ |
| Create project | `POST /projects/create/` | ✓ | | |
| Add member | `POST /projects/<id>/members/` | ✓ | | |
| Create label | `POST /projects/<id>/labels/` | ✓ | | |
| Upload images | `POST /projects/<id>/upload/` | ✓ | ✓ | |
| Assign task | `POST /tasks/<id>/assign/` | ✓ | ✓ | |
| Save/submit annotation | `POST /tasks/<id>/save/` | ✓ | if assignee | if assignee |
| Restore annotation version | `POST /tasks/<id>/restore/` | ✓ | if assignee | if assignee |
| Review task | `POST /tasks/<id>/review/` | ✓ | ✓ | |
| Comment on task | `POST /tasks/<id>/comment/` | ✓ | ✓ | ✓ |
| Next submitted task | `GET /projects/<id>/review-next/` | ✓ | ✓ | |
| Spot-check approved task | `GET /projects/<id>/spot-check/` | ✓ | ✓ | |
| Create release | `POST /projects/<id>/releases/` | ✓ | ✓ | |
| View pages/assets | `GET …` | any workspace member | | |
| Download release | `GET /releases/<uuid>/` | any workspace member | | |
| Download COCO export | `GET /releases/<uuid>/coco/` | any workspace member | | |
| Bulk assign | `POST /projects/<id>/assign/` | ✓ | ✓ | |
| Distribute evenly | `POST /projects/<id>/distribute/` | ✓ | ✓ | |
| Set geometry lock | `POST /projects/<id>/geometry/` | ✓ | | |
| Archive/restore label | `POST /projects/<id>/labels/<lid>/archive/` | ✓ | | |
| Create/revoke API token | `POST /projects/<id>/tokens/…` | ✓ | | |
| Manage webhooks | `POST /projects/<id>/webhooks/…` | ✓ | | |
| Export current work (zip) | `GET /projects/<id>/export/` | ✓ | ✓ | |

> **Superusers** act as `owner` of every workspace — full read/write on
> all projects, tasks, assets, and releases without a membership row.

## Audit events

`project.created` · `member.added` · `schema.label_created` ·
`task.uploaded` · `task.assigned` · `annotation.saved` ·
`task.submitted` · `review.approved` · `review.changes_requested` ·
`release.created` · `project.geometry_changed` · `task.comment` ·
`annotation.restored` · `quality.sampled` · `schema.label_archived` ·
`schema.label_restored` · `api.token_created` · `api.token_revoked` ·
`webhook.created` · `webhook.deleted` · `tasks.distributed`

Each records `project`, `task` (nullable), `actor`, `action`, `payload`,
`created_at`. Save/submit payloads embed the full annotation +
`schema_version` — the release provenance is built from these.
