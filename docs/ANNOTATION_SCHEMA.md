# Annotation Schema — `annotation-studio/v1`

Formal contract for the annotation document stored on each `Task`, the
label ontology, and the release snapshot.

## 1. Annotation document

Stored in `Task.annotation` (JSONB). Top level:

```json
{
  "regions":   [ <region>, ... ],    // ≤ 1000
  "relations": [ <relation>, ... ]   // ≤ 2000
}
```

No other top-level keys are allowed.

### Region

```json
{
  "id": "9f3c2a",                     // [a-zA-Z0-9_-]{1,80}, unique within doc
  "label": "3fa85f64-5717-4562-b3fc-2c963f66afa6",  // Label UUID, not archived
  "type": "box",                      // box | polygon | point
  "points": [[0.1, 0.1], [0.4, 0.6]],
  "attributes": { "state": "on" }     // keys ⊆ label.attributes; values str ≤1000
}
```

Geometry (all coordinates are finite numbers normalized to `[0, 1]`,
origin top-left, x→right, y→down):

| type | points | rule |
|---|---|---|
| `box` | exactly 2 corners | `p0.x < p1.x` and `p0.y < p1.y` (positive area) |
| `polygon` | 3–500 vertices | ordered, implicitly closed |
| `point` | exactly 1 | — |

### Relation

```json
{ "from": "9f3c2a", "to": "b71d0e", "key": "has_plate" }
```

- Exactly these three keys; `from`/`to` reference existing region ids;
  `from ≠ to`; `key` matches `[-a-zA-Z0-9_]{1,64}`.

### JSON Schema (Draft 2020-12)

```json
{
  "$id": "https://godezk.dev/schemas/annotation/v1",
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "regions": {
      "type": "array", "maxItems": 1000,
      "items": {
        "type": "object", "additionalProperties": false,
        "required": ["id", "label", "type", "points"],
        "properties": {
          "id":    { "type": "string", "pattern": "^[a-zA-Z0-9_-]{1,80}$" },
          "label": { "type": "string", "format": "uuid" },
          "type":  { "enum": ["box", "polygon", "point"] },
          "points": {
            "type": "array",
            "items": {
              "type": "array", "minItems": 2, "maxItems": 2,
              "items": { "type": "number", "minimum": 0, "maximum": 1 }
            }
          },
          "attributes": {
            "type": "object",
            "propertyNames": { "pattern": "^[a-zA-Z_][a-zA-Z0-9_]{0,39}$" },
            "additionalProperties": { "type": "string", "maxLength": 1000 }
          }
        }
      }
    },
    "relations": {
      "type": "array", "maxItems": 2000,
      "items": {
        "type": "object", "additionalProperties": false,
        "required": ["from", "to", "key"],
        "properties": {
          "from": { "type": "string" },
          "to":   { "type": "string" },
          "key":  { "type": "string", "pattern": "^[a-zA-Z0-9_-]{1,64}$" }
        }
      }
    }
  }
}
```

Cross-field rules enforced server-side (not expressible above): unique
region ids, `from`/`to` resolvable + distinct, label exists and is not
archived, attribute keys ⊆ label's declared keys, box positive area,
polygon 3–500 points.

## 2. Label schema (ontology)

```json
{
  "id": "uuid",
  "key": "vehicle",                  // [-a-zA-Z0-9_]{1,64}, unique per project
  "name": "Vehicle",                 // ≤ 80
  "color": "#206e69",                // #[0-9a-fA-F]{6}
  "attributes": [                    // ≤ 20, unique keys
    { "key": "vehicle_type", "options": ["car", "truck", "bus"] },
    { "key": "damaged" }                                   // free text
  ],
  "archived": false
}
```

## 3. Release snapshot

`Release.snapshot` — immutable once created:

```json
{
  "format": "annotation-studio/v1",
  "project": "Device detection",
  "schema_version": 3,
  "labels":   [ <label> ... ],
  "tasks": [
    {
      "id": 12, "filename": "img_0042.png",
      "digest": "sha256…", "width": 1920, "height": 1080,
      "revision": 4, "annotation": { "regions": [...], "relations": [...] }
    }
  ],
  "provenance": [
    { "task_id": 12, "actor__username": "worker1",
      "action": "annotation.saved", "payload": {...}, "created_at": "…" }
  ]
}
```

Only `approved` tasks are included. Downloaded verbatim as
`release-<uuid>.json` (indented). Denormalized pixel coordinates =
`normalized × width|height`.

## 4. COCO export (`/releases/<uuid>/coco/`)

On-the-fly transformation of the snapshot (pixel coordinates, rounded to
2 decimals):

- `categories[]` — `{id: <1-based seq>, name, supercategory: label.key}`
- `images[]` — `{id: task_id, file_name, width, height}`
- `annotations[]` — sequential `id`, `image_id`, `category_id`,
  `iscrowd: 0`, `attributes` (passthrough), plus `region_id`/`region_type`
  extension fields:
  - box → `bbox [x, y, w, h]`, `area`
  - polygon → `segmentation [[x0,y0,...]]`, shoelace `area`
  - point → `keypoints [x, y, 2]`, `num_keypoints: 1`, 2px `bbox`/`area`
- `relations[]` — extension array `{from, to, key, image_id}` with
  region ids translated to annotation ids
- `info` — project name, release name, schema_version,
  `source_format: annotation-studio/v1`, `date_created`
