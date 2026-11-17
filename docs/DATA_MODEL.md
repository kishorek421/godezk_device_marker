# Database Model

Django ORM in `studio/models.py`. SQLite (dev) or Postgres (prod).
UUIDs on `Label` and `Release` so exports never expose sequential ids.

```
┌─────────────┐         ┌──────────────┐        ┌──────────┐
│    User     │◄───┐    │  Workspace   │◄──┐    │          │
│ (django)    │    │    │──────────────│   │    │          │
└─────────────┘    │    │ name         │   └─── │ (other   │
      ▲            │    │ created_at   │        │  FKs)    │
      │            │    └──────────────┘        └──────────┘
      │            │           ▲
      │            │           │
      │         ┌──┴───────────┴───┐    ┌───────────────┐
      └────────►│   Membership     │    │   Project     │
       user FK  │──────────────────│    │───────────────│
                │ workspace FK     │    │ workspace FK  │
                │ role: owner |    │    │ name          │
                │   reviewer |     │    │ instructions  │
                │   annotator      │    │ schema_version│
                │ UNIQUE(ws,user)  │    │ created_at    │
                └──────────────────┘    └───┬───────────┘
                                            │
              ┌──────────────────┬──────────┼──────────┬────────────┐
              ▼                  ▼          ▼          ▼            ▼
        ┌───────────┐     ┌──────────┐ ┌─────────┐ ┌────────┐ ┌──────────┐
        │  Label    │     │   Task   │ │  Event  │ │Release │ │          │
        │───────────│     │──────────│ │─────────│ │────────│ │          │
        │ id (uuid) │     │ projectFK│ │projectFK│ │id(uuid)│ │          │
        │ projectFK │     │ image    │ │ task FK │ │project │ │          │
        │ key*      │     │ filename │ │ actor FK│ │ name   │ │          │
        │ name      │     │ digest   │ │ action  │ │snapshot│ │          │
        │ color     │     │ width/ht │ │ payload │ │creator │ │          │
        │attributes │     │ state    │ │created  │ │created │ │          │
        │ archived  │     │ assignee │ └─────────┘ └────────┘ │          │
        │UNIQUE(p,k)│     │ revision │                        │          │
        └───────────┘     │annotation│◄───────────────────────┘          │
                          │updated_at│   Task FK on Event               │
                          │UNIQUE(p, │                                   │
                          │ digest)  │                                   │
                          └──────────┘                                   │
```

## Field notes

- **Membership.role** — `owner | reviewer | annotator`; one row per
  (workspace, user).
- **Project.schema_version** — incremented on every label creation;
  recorded in save events so consumers know which ontology an annotation
  was drawn against.
- **Label.attributes** — JSON list. Contract: `[{key, options?}]`;
  currently stored as `[key]` strings (gap G1).
- **Task.digest** — SHA-256 of the original upload bytes; unique per
  project → upload-time dedupe.
- **Task.revision** — optimistic-lock counter, incremented on every
  successful save/assign/review.
- **Task.annotation** — the `annotation-studio/v1` document
  (see ANNOTATION_SCHEMA.md).
- **Task.image** — `ImageField` under `private_uploads/images/%Y/%m/`,
  always re-encoded JPEG; never served statically.
- **Event.payload** — action-specific; save/submit embed the full
  annotation + schema_version (used for release provenance).
- **Release.snapshot** — immutable JSON; a point-in-time export.
- **Job** (Phase 5) — durable background queue: `type`, `payload` (JSON),
  `status` (`pending|running|done|dead`), `attempts`/`max_attempts`,
  `run_after` (backoff), `error`, `result`. Workers claim jobs
  atomically; crashed workers' jobs are reclaimed after 10 min.

## Scale posture

Beyond PKs/uniques: `Task(project,state)` and `Task(project,assignee)`
for the queue/assignment hot paths, `Event(project,created_at)` and
`Event(project,action)` for audit/progress queries, and
`Job(status,run_after)` for the worker poll. SQLite runs WAL +
`busy_timeout` so concurrent writers serialize instead of failing.
