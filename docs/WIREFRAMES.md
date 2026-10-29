# Wireframes

ASCII layouts matching the shipped templates (`studio/templates/`).

## 1. Home — `/` (workspace overview)

```
┌────────────────────────────────────────────────────────────────────┐
│ GODEZK MARKER · WORKSPACE OVERVIEW                        [4 projects]│
│ Build better datasets.                                              │
├──────────────────────────────────────────────┬─────────────────────┤
│ YOUR PROJECTS                                │ NEW PROJECT         │
│ ┌────────────┐ ┌────────────┐ ┌────────────┐ │ ┌─────────────────┐ │
│ │ [D]        │ │ [S]        │ │ [+]        │ │ │ Workspace   [v] │ │
│ │ WS·IMAGE   │ │ WS·IMAGE   │ │            │ │ │ Name  [______]  │ │
│ │ Devices    │ │ Switches   │ │  clean     │ │ │ Instructions    │ │
│ │ instr…     │ │ instr…     │ │  canvas    │ │ │ [____________]  │ │
│ │ 42 tasks·v3│ │ 12 tasks·v1│ │            │ │ │ [Create →]      │ │
│ └────────────┘ └────────────┘ └────────────┘ │ └─────────────────┘ │
│                                              │ CREATE WORKSPACE    │
│                                              │ ┌─────────────────┐ │
│                                              │ │ Name  [______]  │ │
│                                              │ │ [Create]        │ │
│                                              │ └─────────────────┘ │
└──────────────────────────────────────────────┴─────────────────────┘
```

## 2. Project — `/projects/<id>/`

```
┌────────────────────────────────────────────────────────────────────┐
│ ‹ Workspace /                          SCHEMA V3        [Owner]    │
│ Device detection                       instructions…              │
│ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐                     │
│ │ 42      │ │ 5       │ │ 4       │ │ 2       │                     │
│ │ items   │ │ labels  │ │ members │ │releases │                     │
│ └─────────┘ └─────────┘ └─────────┘ └─────────┘                     │
├──────────────────────────────────────────────┬─────────────────────┤
│ ANNOTATION QUEUE     [search][state v][Filter]│ LABELS (+ create)  │
│ ┌──────────────────────────────────────────┐ │ ┌─────────────────┐ │
│ │ img_0042.png  1920×1080  rev4            │ │ │ ● vehicle       │ │
│ │ [In progress] [assignee v][Assign] [Open]│ │ │ ● plate         │ │
│ ├──────────────────────────────────────────┤ │ │ key/name/color/ │ │
│ │ img_0043.png                             │ │ │ attributes form │ │
│ │ [Submitted]   worker1            [Open]  │ │ └─────────────────┘ │
│ │ …up to 200 rows                          │ │ MEMBERS (+ add)    │
│ └──────────────────────────────────────────┘ │ UPLOAD (file pick) │
│                                              │ RELEASES (+ create)│
│                                              │ ACTIVITY (events)  │
└──────────────────────────────────────────────┴─────────────────────┘
```

## 3. Editor — `/tasks/<id>/`

```
┌────────────────────────────────────────────────────────────────────┐
│ ‹ Project / Annotation editor                                      │
│ img_0042.png  1920×1080 · Assigned to worker1                      │
│                         [In progress] Saved · rev 4 [Save][Submit] │
├──────────────────────────────────────────────────┬─────────────────┤
│ [Select V][Box B][Polygon P][Point K]│[Undo][Redo]│ LABEL KEY       │
│ [− 100% +]                           │           │ [vehicle     v] │
│ ┌──────────────────────────────────┐ │           ├─────────────────┤
│ │                                  │ │           │ REGIONS (3)     │
│ │        ┌──────────┐              │ │           │ ▸1. Vehicle·box │
│ │        │ ▓▓▓▓▓▓▓▓  │   Vehicle   │ │           │  2. Plate·poly  │
│ │   img  │ ▓ car ▓  │ ← box       │ │           │  3. Lamp·point  │
│ │        └──────────┘              │ │           ├─────────────────┤
│ │   ╱──── polygon                  │ │           │ REGION ATTRS    │
│ │  ╱ plate                         │ │           │ state [on____]  │
│ │   • point                        │ │           │ type [car___]   │
│ │                                  │ │           ├─────────────────┤
│ │   (SVG overlay, viewBox 0-1000,  │ │           │ RELATIONS       │
│ │    coords normalized 0..1)       │ │           │ 1 ─has_plate→ 2 │
│ │                                  │ │           │ from[to][key]+  │
│ └──────────────────────────────────┘ │           ├─────────────────┤
│ hint: dbl-click finishes polygon     │           │ [Delete region] │
└──────────────────────────────────────────────────┴─────────────────┘
```

Editor behavior contract:

- Tools: Select (V), Box (B), Polygon (P), Point (K); Esc cancels a
  draft polygon, Enter/double-click finishes it.
- Select tool: click to select, drag to move (clamped to bounds).
- Undo/redo stack (80 deep): Ctrl+Z / Ctrl+Shift+Z.
- Save: Ctrl+S, explicit button, and autosave every 5 s when dirty;
  optimistic-lock revision, conflict ⇒ block + reload prompt.
- Unsaved work survives a crash via localStorage recovery keyed by
  `godezk-marker:<task_id>` + revision.
- `beforeunload` guard while dirty/saving.
- Submit requires confirmation; after submit the task locks.
