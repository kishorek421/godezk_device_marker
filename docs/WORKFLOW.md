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

## Concurrency contract

Every mutating call carries the caller's seen `revision`. The server
applies `UPDATE … WHERE id=? AND revision=? AND state=?` atomically and
returns **409** if zero rows matched (someone else saved first). The
editor autosaves every 5 s and blocks further saves after a conflict
until reload.

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
| Review task | `POST /tasks/<id>/review/` | ✓ | ✓ | |
| Create release | `POST /projects/<id>/releases/` | ✓ | ✓ | |
| View pages/assets | `GET …` | any workspace member | | |
| Download release | `GET /releases/<uuid>/` | any workspace member | | |

## Audit events

`project.created` · `member.added` · `schema.label_created` ·
`task.uploaded` · `task.assigned` · `annotation.saved` ·
`task.submitted` · `review.approved` · `review.changes_requested` ·
`release.created`

Each records `project`, `task` (nullable), `actor`, `action`, `payload`,
`created_at`. Save/submit payloads embed the full annotation +
`schema_version` — the release provenance is built from these.
