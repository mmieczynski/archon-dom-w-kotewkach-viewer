# Work breakdown

Each task in `tasks/` is written as a **self-contained subagent brief**: goal, inputs,
deliverables, acceptance criteria, dependencies. A task file should be usable as a
subagent prompt with no additional context beyond `README.md` and `TESTS.md`.

## Task index

| ID | Task | Depends on | Parallelisable with |
|---|---|---|---|
| [T01](tasks/T01.md) | ~~Repo scaffold & tooling~~ **DONE** | — | T02, T03 |
| [T02](tasks/T02.md) | ~~Spec JSON Schema~~ **DONE** | — | T01, T03 |
| [T03](tasks/T03.md) | ~~Source data acquisition~~ **DONE** | — | T01, T02 |
| [T04](tasks/T04.md) | Transcribe ground floor | T02, T03 | T05 |
| [T05](tasks/T05.md) | Transcribe attic | T02, T03 | T04 |
| [T06](tasks/T06.md) | **Test 1** — chain closure | T02 | T07 |
| [T07](tasks/T07.md) | Geometry kernel (2D) | T02 | T06 |
| [T08](tasks/T08.md) | **Test 2** — room areas | T07 | T09, T10 |
| [T09](tasks/T09.md) | **Test 3** — global invariants | T07, T11 | T08, T10 |
| [T10](tasks/T10.md) | **Test 4** — topology & sanity | T07 | T08, T09 |
| [T11](tasks/T11.md) | 3D generator | T07 | — |
| [T12](tasks/T12.md) | glTF export + **Test 5** mesh checks | T11 | T13, T14 |
| [T13](tasks/T13.md) | **Test 6** — orthographic overlay | T11, T03 | T12, T14 |
| [T14](tasks/T14.md) | three.js browser viewer | T12 | T13 |
| [T15](tasks/T15.md) | ~~Confirm finish allowance~~ **SUPERSEDED** — see below | T04, T07 | — |
| [T16](tasks/T16.md) | CI wiring & build pipeline | T06, T08–T13 | — |
| [T17](tasks/T17.md) | ~~Resolve roof discrepancy~~ **DONE** — 35° confirmed | T03 | — |
| [T18](tasks/T18.md) | Resolve three residuals against the images | T03, T05, T08 | T09, T12, T13 |

## Dependency graph

```
T01 ─┐
T02 ─┼─────────────────────────────────────────────┐
T03 ─┘                                             │
 │                                                 │
 ├─→ T04 ─┬─→ T15 (norm spike)                     │
 ├─→ T05 ─┘                                        │
 │                                                 │
 ├─→ T06 (chain closure)  ────────────────────┐    │
 └─→ T07 (geometry kernel)                    │    │
       ├─→ T08 (room areas)   ────────────────┤    │
       ├─→ T10 (topology)     ────────────────┤    │
       └─→ T11 (3D generator)                 │    │
             ├─→ T09 (invariants) ────────────┤    │
             ├─→ T12 (export + mesh) ─────────┤    │
             │     └─→ T14 (viewer)           │    │
             └─→ T13 (overlay) ───────────────┴─→ T16 (CI)
```

## Suggested execution waves

**Wave 1 (fully parallel, 3 agents):** T01, T02, T03
**Wave 2 (parallel, 4 agents):** T04, T05, T06, T07
**Wave 3 (parallel, 4 agents):** T08, T10, T11, T15
**Wave 4 (parallel, 4 agents):** T09, T12, T13, T18
**Wave 5:** T14, T16

**Golden-image sign-off is deferred to the very end** (user instruction, 2026-08-31). T13
generates overlays into `build/` and leaves `tests/golden/` empty; the overlay test skips
with a message saying goldens are pending human sign-off. This is deliberate — auto-accepted
goldens are worthless — and it is the last gate before the project is done.

**The repository stays local: no remote, no hosted CI** (user instruction, 2026-08-31).
`data/source/` contains Archon's copyrighted plan bitmaps, so publishing the repo would
redistribute them. T16 therefore wires the build and checks through `justfile` plus a local
pre-commit hook, not GitHub Actions.

## T15 is superseded, not skipped

T15 asked two questions and both are answered, so running it would re-derive a settled
result:

- **Which measurement norm?** PN-ISO 9836, *w świetle ścian*, banded 1.4 m / 2.2 m —
  resolved by T03 and corroborated by the `140`/`220` contour labels printed on the attic
  plan itself.
- **What finish allowance?** 20 mm per face. T08 swept the allowance across all 18 area
  equations and got a convex curve with its optimum at 19 mm; 20 mm is indistinguishable
  (RMS 0.64% vs 0.63%) and is the round number an architect would use. The sweep is
  runnable: `uv run python tests/test_room_areas.py --sweep`.

The evidence lives in `README.md` rather than in a T15 report. What T15 was really guarding
against — a *uniform* offset across every room, which would mean the norm was wrong rather
than the geometry — did not occur: the residuals are scattered and room-specific.

## Critical sequencing constraint

**T15 (norm resolution) gates the meaning of every area assertion.** If T08 fails with a
uniform offset across all rooms, stop and run T15 before touching any room geometry. The
task ordering above puts T15 in wave 3 deliberately — it needs real transcribed data to
resolve, but its answer retro-actively validates T08's tolerance choice.

## File ownership

Tasks run concurrently as subagents. **Two agents writing the same file clobber each
other**, so ownership is exclusive: only the listed owner writes to a given path.

| Path | Owner |
|---|---|
| `pyproject.toml`, `justfile`, `.gitignore` | T01 |
| `src/kotewki/*.py` (empty stubs only) | T01 |
| `spec/schema.json`, `spec/meta.json`, `src/kotewki/spec.py` | T02 |
| `data/source/`, `data/published.json` | T03 |
| `spec/ground.json` | T04 |
| `spec/attic.json` | T05 |
| `src/kotewki/geometry.py`, `quantities.py` | T07 |
| `src/kotewki/generator.py` | T11 |
| `src/kotewki/export.py` | T12 |
| `tests/test_*.py` | one module per test task, no overlap |
| `tests/golden/` | T13 |
| `viewer/` | T14 |
| `hooks/`, `build/REPORT.md`, `justfile` (after T01) | T16 |
| `docs/T18-findings.md` | T18 |

**T01 creates empty module stubs only.** If it writes real content into `spec.py` or
`geometry.py`, T02 and T07 will overwrite it and that work is wasted.

### Cross-lane results route through the coordinator

Two tasks produce values that land in a file they do not own. **They must not write it
directly** — they report a recommendation, and the coordinator applies it:

| Task | Produces | Lands in | Owned by |
|---|---|---|---|
| T17 | `roof.pitch_deg`, `eaves_overhang`, `springing` | `spec/meta.json` | T02 |
| T15 | `construction.finish_allowance`, `measurement_norm` | `spec/meta.json` | T02 |

`meta.json` carries `disputed: true` + `dispute_ref` on the roof block until T17 lands, so
the unresolved state is explicit in the data rather than only in prose.

## Rules for every task

1. **Never hand-edit `build/`.** It is generated. Fix the spec.
2. **Integer millimetres in the spec.** Metres only at glTF export.
3. **Derived values are marked `derived: true`** and must never be presented as
   transcribed source fact. See "Known unknowns" in `README.md`.
4. **A failing test is a finding, not a blocker to route around.** Do not loosen a
   tolerance to make a test pass. Report the discrepancy.
5. **Report honestly.** If a task is partially complete, say which part and why.
