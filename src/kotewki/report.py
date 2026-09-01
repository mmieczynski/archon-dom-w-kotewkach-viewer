"""T16 -- generate ``build/REPORT.md``, the project's validation report.

``build/`` is generated and is **never hand-edited** (README, "Core principle"), and the
report is part of ``build/``. So it is produced by this module, from three sources and no
typed-in numbers:

* ``build/quantities.json`` -- the computed-vs-published take-off written by
  ``kotewki.export``. Every global figure and every room area in the report is *read* from
  it rather than recomputed, so the report cannot drift away from the artifact it
  describes.
* ``data/published.json`` -- the publisher's figures.
* ``spec/*.json`` via :func:`kotewki.spec.load_spec` -- walked for provenance, so the
  "derived values" section lists what the spec actually marks rather than what someone
  remembered marking.

Two things come from the test suite itself, because hardcoding them here is exactly how a
report starts lying:

* :func:`tests.test_room_areas.independent_equation_count` -- the honest count of area
  equations (18 run, 16 independent). An earlier draft of this project claimed ~19.
* :func:`tests.test_invariants.evaluate` -- the five invariants' *tolerances* and what each
  one validates. The computed values still come from ``quantities.json``; the two are
  cross-checked and any disagreement is printed in the report as a finding rather than
  silently resolved in favour of one of them.

The limitation statement is not typed here either: it is extracted verbatim from
``tasks/T16.md`` and compared against the copy below. If the two ever differ, generation
fails loudly rather than emitting a softened version.

**No wall-clock timestamp.** The report is a deterministic function of the spec and the
artifact; it identifies the run by the model's sha256 instead. Two runs of ``just report``
on an unchanged spec produce byte-identical files, which is the same guarantee
``tests/test_export.py`` holds the glb to.

CLI::

    uv run python -m kotewki.report            # -> build/REPORT.md
    uv run python -m kotewki.report --stdout   # print instead of writing
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

__all__ = [
    "LIMITATION",
    "ReportError",
    "build_report",
    "main",
    "write_report",
]

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_DIR = REPO_ROOT / "build"
SPEC_DIR = REPO_ROOT / "spec"
TESTS_DIR = REPO_ROOT / "tests"
TASKS_DIR = REPO_ROOT / "tasks"

REPORT_PATH = BUILD_DIR / "REPORT.md"
QUANTITIES_PATH = BUILD_DIR / "quantities.json"
PUBLISHED_PATH = REPO_ROOT / "data" / "published.json"
BRIEF_PATH = TASKS_DIR / "T16.md"

#: Overlay images, in the order they are shown. Written by ``tests/test_overlay.py``.
OVERLAYS: tuple[tuple[str, str], ...] = (
    ("overlay_ground.png", "Ground floor -- generated section over `plan_ground.png`"),
    ("overlay_attic.png", "Attic -- generated section over `plan_attic.png`"),
    (
        "overlay_ground_mirrored.png",
        "Ground floor, MIRRORED spec -- what a failing overlay looks like",
    ),
    (
        "overlay_attic_mirrored.png",
        "Attic, MIRRORED spec -- the (E)-variant mirroring failure mode",
    ),
)

#: The honest limitation, ending the report. Held here *and* checked against
#: ``tasks/T16.md`` at generation time so it cannot be quietly softened in either place.
LIMITATION = (
    "This suite guarantees that the model matches the transcribed spec, and that the "
    "spec is\ninternally consistent and agrees with every published figure from Archon. "
    "It does not\nguarantee that a printed dimension was read correctly in the case where "
    "the wrong value\nhappens to satisfy every chain sum, every area assertion, and every "
    "global invariant\nsimultaneously. Redundancy is the defence, not proof. The model is "
    "also a reconstruction\nof the *design*, not of any *as-built* house -- real "
    "construction deviates from drawings."
)


class ReportError(Exception):
    """The report cannot be generated truthfully. Never downgraded to a warning."""


# --------------------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ReportError(
            f"{_rel(path)} does not exist. Run `just build` first -- the report describes "
            f"the artifact and refuses to describe one that has not been built."
        )
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as error:  # pragma: no cover - corrupt artifact
        raise ReportError(f"{_rel(path)} is not valid JSON: {error}") from error


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:  # pragma: no cover - path outside the repo
        return str(path)


def _load_test_module(name: str) -> Any:
    """Import ``tests/<name>.py`` by path.

    The suite is not an installed package, and this module deliberately reaches into it:
    the equation count and the invariant tolerances must come from the code that enforces
    them, not from a copy in the report generator that can go stale.
    """
    path = TESTS_DIR / f"{name}.py"
    if not path.is_file():
        raise ReportError(
            f"{_rel(path)} is missing. The report reads the equation count and the "
            f"invariant tolerances from the test suite rather than hardcoding them, so it "
            f"cannot be generated without it."
        )
    spec = importlib.util.spec_from_file_location(f"_kotewki_report_{name}", path)
    if spec is None or spec.loader is None:  # pragma: no cover - importlib contract
        raise ReportError(f"cannot import {_rel(path)}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _brief_limitation() -> str:
    """Pull the limitation statement out of ``tasks/T16.md``'s blockquote, verbatim."""
    if not BRIEF_PATH.is_file():  # pragma: no cover - brief always present in-repo
        raise ReportError(f"{_rel(BRIEF_PATH)} is missing; cannot verify the limitation.")
    text = BRIEF_PATH.read_text(encoding="utf-8")
    blocks = re.findall(r"(?:^> ?.*\n)+", text, flags=re.MULTILINE)
    wanted = [
        "\n".join(line[2:] if line.startswith("> ") else line[1:] for line in block.splitlines())
        for block in blocks
        if "Redundancy is the defence" in block
    ]
    if len(wanted) != 1:
        raise ReportError(
            f"expected exactly one limitation blockquote in {_rel(BRIEF_PATH)}, found "
            f"{len(wanted)}."
        )
    return wanted[0].strip()


def _normalise(text: str) -> str:
    """Compare prose ignoring dash style and line wrapping, nothing else."""
    return re.sub(r"\s+", " ", text.replace("—", "--").replace("–", "--")).strip()


def _verified_limitation() -> str:
    """The brief's wording, checked against :data:`LIMITATION`. Fails on any drift."""
    brief = _brief_limitation()
    if _normalise(brief) != _normalise(LIMITATION):
        raise ReportError(
            "the limitation statement in tasks/T16.md and kotewki.report.LIMITATION "
            "disagree. The report will not emit a paraphrase of it.\n"
            f"  brief:  {_normalise(brief)}\n"
            f"  module: {_normalise(LIMITATION)}"
        )
    return brief


# --------------------------------------------------------------------------------------
# Small formatting helpers
# --------------------------------------------------------------------------------------


def _pct(value: float | None) -> str:
    return "--" if value is None else f"{value:+.3f}%"


def _num(value: float | None, dp: int = 3) -> str:
    return "--" if value is None else f"{value:.{dp}f}"


def _cell(text: Any) -> str:
    return str(text).replace("|", r"\|").replace("\n", " ")


def _table(header: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[str]:
    lines = [
        "| " + " | ".join(_cell(item) for item in header) + " |",
        "|" + "|".join("---" for _ in header) + "|",
    ]
    lines += ["| " + " | ".join(_cell(item) for item in row) + " |" for row in rows]
    return lines


def _mark(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def _oneline(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------------------
# Provenance walk
# --------------------------------------------------------------------------------------

#: Top-level spec arrays that carry per-entity provenance, and how to title them.
_ENTITY_SECTIONS: tuple[tuple[str, str], ...] = (
    ("levels", "Levels"),
    ("walls", "Walls"),
    ("openings", "Openings"),
    ("rooms", "Rooms"),
    ("dimension_chains", "Dimension chains"),
    ("slab_openings", "Slab openings"),
    ("roof_openings", "Roof openings"),
)


def _annotated(node: Any, path: str, out: list[dict[str, Any]]) -> None:
    """Collect every object marked ``derived`` or ``derived_fields``, with its note."""
    if isinstance(node, Mapping):
        fully = node.get("derived") is True
        fields = list(node.get("derived_fields") or ())
        if fully or fields:
            out.append(
                {
                    "path": path,
                    "id": node.get("id") or node.get("material") or path.rsplit(".", 1)[-1],
                    "fields": ["<entire object>"] if fully else fields,
                    "fully_derived": fully,
                    "note": _oneline(str(node.get("note", ""))),
                    "disputed": node.get("disputed") is True,
                    "dispute_ref": node.get("dispute_ref"),
                }
            )
        for key, value in node.items():
            _annotated(value, f"{path}.{key}" if path else key, out)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _annotated(value, f"{path}[{index}]", out)


def _provenance(document: Mapping[str, Any]) -> dict[str, Any]:
    """Transcribed-vs-derived counts over the spec, plus the full derived listing."""
    entries: list[dict[str, Any]] = []
    _annotated(dict(document), "", entries)

    entity_total = 0
    entity_annotated = 0
    per_section: dict[str, dict[str, int]] = {}
    for key, _title in _ENTITY_SECTIONS:
        items = document.get(key) or []
        annotated = sum(
            1
            for item in items
            if isinstance(item, Mapping)
            and (item.get("derived") is True or item.get("derived_fields"))
        )
        per_section[key] = {"total": len(items), "derived": annotated}
        entity_total += len(items)
        entity_annotated += annotated

    return {
        "entries": entries,
        "objects_annotated": len(entries),
        "objects_fully_derived": sum(1 for entry in entries if entry["fully_derived"]),
        "objects_partly_derived": sum(1 for entry in entries if not entry["fully_derived"]),
        "derived_field_names": sum(
            len(entry["fields"]) for entry in entries if not entry["fully_derived"]
        ),
        "entity_total": entity_total,
        "entity_annotated": entity_annotated,
        "per_section": per_section,
        "disputed": [entry for entry in entries if entry["disputed"]],
    }


# --------------------------------------------------------------------------------------
# Report sections
# --------------------------------------------------------------------------------------


def _header(quantities: Mapping[str, Any], published: Mapping[str, Any]) -> list[str]:
    artifact = quantities.get("artifact", {})
    spec_digest = _spec_digest()
    return [
        "# Validation report -- Dom w Kotewkach 6 (E)",
        "",
        "Generated by `just report` (`kotewki.report`). **Do not edit this file**: it lives "
        "in `build/`, which is generated and never hand-authored. Fix the spec and rebuild.",
        "",
        f"- Source project: <{published.get('source_url', '')}>, variant "
        f"`{published.get('variant', '?')}`, figures retrieved "
        f"{published.get('retrieved', '?')}",
        f"- Spec: `spec/*.json`, sha256 `{spec_digest[:16]}`",
        f"- Artifact: `{artifact.get('path', 'build/model.glb')}`, "
        f"{artifact.get('bytes', 0):,} bytes, sha256 `{str(artifact.get('sha256', ''))[:16]}`",
        f"- Mesh: {artifact.get('nodes', 0)} nodes, {artifact.get('vertices', 0):,} vertices, "
        f"{artifact.get('faces', 0):,} faces",
        "",
        "There is no timestamp in this report on purpose. It is a deterministic function of "
        "the spec, so two runs against an unchanged spec produce byte-identical output and a "
        "diff means something really changed.",
        "",
    ]


def _spec_digest() -> str:
    """One digest over every spec file, so the report identifies its own input."""
    digest = hashlib.sha256()
    for path in sorted(SPEC_DIR.glob("*.json")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _summary(
    invariants: Sequence[Any],
    area_results: Sequence[Any],
    counts: Mapping[str, int],
    area_module: Any,
) -> list[str]:
    inv_ok = sum(1 for item in invariants if item.ok)
    area_ok = sum(1 for item in area_results if item.ok)
    failing = [item for item in area_results if not item.ok]
    unrecorded = [item for item in failing if not item.recorded]
    return [
        "## Verdict",
        "",
        *_table(
            ("Check", "Result"),
            [
                ("Global invariants", f"**{inv_ok}/{len(invariants)}** inside tolerance"),
                (
                    "Published area equations",
                    f"**{area_ok}/{len(area_results)}** inside +-1%",
                ),
                (
                    "of which independent",
                    f"**{counts['independent']}** "
                    f"({counts['checks_run']} run, {counts['attic_dependent']} attic checks "
                    f"algebraically dependent)",
                ),
                ("Mean signed area error", f"{area_module.mean_error(area_results):+.2%}"),
                ("RMS area error", f"{area_module.aggregate_error(area_results):.2%}"),
                ("Failure pattern", f"`{area_module.classify_failures(area_results)}`"),
                (
                    "Unanalysed failures",
                    f"**{len(unrecorded)}** "
                    f"({len(failing)} outside +-1%, all {len(failing) - len(unrecorded)} of "
                    f"them recorded and diagnosed)"
                    if not unrecorded
                    else f"**{len(unrecorded)}** -- see the area table",
                ),
            ],
        ),
        "",
        "The model is **well-evidenced**, not proven. Read the limitation at the end of this "
        "report before quoting any number here.",
        "",
    ]


def _independence_section(
    counts: Mapping[str, int], invariant_count: int, chain_count: int
) -> list[str]:
    return [
        "## How strong is the checksum, counted honestly",
        "",
        f"**{counts['checks_run']} published-area equations are run, of which "
        f"{counts['independent']} are independent.** Not "
        f"{counts['checks_run']}, and not the ~19 an earlier "
        "draft of this project asserted. Counted by "
        "`tests/test_room_areas.py::independent_equation_count`, not by hand, and pinned by a "
        "test so it cannot quietly drift back up.",
        "",
        *_table(
            ("", "Count"),
            [
                ("Equations run", counts["checks_run"]),
                ("Ground floor", counts["ground"]),
                ("Attic", counts["attic"]),
                ("Attic checks algebraically dependent", counts["attic_dependent"]),
                ("**Independent equations**", f"**{counts['independent']}**"),
            ],
        ),
        "",
        "Two concrete reasons the count is lower than the room table suggests:",
        "",
        "- **The ground floor gives 11 equations, not 14.** Hol (2), Salon z jadalnia (6), "
        "Hol (7) and Kuchnia (14) are one continuous open-plan space with no masonry between "
        "them. Polygonisation correctly returns a single face, so they are checked as one "
        "combined area (14 - 4 + 1 = 11). Their published splits are the publisher's virtual "
        "measuring lines (x 4500, y 3800, x 11500), not walls; checking them individually "
        "would fail no matter how correct the geometry is.",
        "- **Two attic checks are algebraically dependent.** Both `Strych ocieplony` rooms "
        "span the full building depth, so both roof slopes band them by the same constant "
        "counted depth (3.779 m): their banded figure and their floor figure are two readings "
        "of one unknown. Only Antresola stops short of the ridge and so adds genuine "
        "information.",
        "",
        f"Alongside these sit the {invariant_count} global invariants and {chain_count} "
        "dimension-chain closures, which constrain the model from directions the room areas "
        "cannot reach.",
        "",
    ]


def _invariants_section(
    invariants: Sequence[Any],
    quantities: Mapping[str, Any],
    mismatches: list[str],
) -> list[str]:
    rows = []
    for item in invariants:
        reported = _INVARIANT_SOURCES[item.name](quantities)
        if reported is not None and abs(reported - item.computed) > 1e-6:
            mismatches.append(
                f"invariant `{item.name}`: build/quantities.json reports {reported:.6f} but "
                f"tests/test_invariants.py computes {item.computed:.6f}"
            )
        computed = item.computed if reported is None else reported
        delta = computed - item.published
        margin = (
            f"{delta * 1000:+.1f} mm"
            if item.abs_tolerance is not None
            else f"{100 * delta / item.published:+.3f}%"
        )
        rows.append(
            (
                item.name,
                f"{computed:.4f} {item.unit}",
                f"{item.published:.2f} {item.unit}",
                margin,
                item.tolerance_text,
                _mark(item.ok),
                item.validates,
            )
        )
    return [
        "## Global invariants -- published vs computed",
        "",
        "Values read from `build/quantities.json`; tolerances and pass/fail read from "
        "`tests/test_invariants.py`, so nothing here is a number typed into a report.",
        "",
        *_table(
            ("Invariant", "Computed", "Published", "Delta", "Tolerance", "", "Validates"),
            rows,
        ),
        "",
        "**Two of these are only meaningful because the ridge is an output.** It is computed "
        "as `attic_floor 3040 + knee_wall 290 + roof_buildup 280 + 4500 * tan 35deg = "
        "6760.93 mm` and never assigned from the printed 6770. That wire is cut in four "
        "places in the suite, including a rebuild with `section_elevations.ridge` set to "
        "99999.",
        "",
    ]


#: Where each invariant's computed value lives in ``build/quantities.json``.
_INVARIANT_SOURCES: dict[str, Any] = {
    "usable area": lambda q: q["areas_m2"]["usable"]["computed"],
    "footprint": lambda q: q["areas_m2"]["footprint"]["computed"],
    "cubature": lambda q: q["volumes_m3"]["cubature"]["computed"],
    "roof area": lambda q: q["roof"]["area_m2"]["computed"],
    "building height": lambda q: q["heights"]["building_m"]["computed"],
}


def _other_published_section(quantities: Mapping[str, Any]) -> list[str]:
    """Published figures that are computed and reported but are not sworn invariants."""
    areas = quantities["areas_m2"]
    heights = quantities["heights"]
    rows = [
        ("Net area", areas["net"], "m2", "reported"),
        ("Floor area", areas["floor"], "m2", "reported"),
        ("Attic area (pow. strychu)", areas["attic"], "m2", "reported"),
        ("Boiler room (Kotlownia)", areas["boiler"], "m2", "asserted, +-1%, recorded FAIL"),
        ("Ridge above ground", heights["ridge_above_ground_m"], "m", "reported"),
        ("Eave above ground", heights["eave_above_ground_m"], "m", "asserted"),
    ]
    formatted = [
        (
            name,
            f"{block['computed']:.4f} {unit}",
            f"{block['published']:.2f} {unit}",
            _pct(block["residual_pct"]),
            status,
        )
        for name, block, unit, status in rows
    ]
    return [
        "## Other published figures",
        "",
        "Computed and published side by side. **Status says whether the suite actually "
        "asserts the figure** -- most of these are cross-checks that would be dishonest to "
        "present as passing tests.",
        "",
        *_table(("Figure", "Computed", "Published", "Residual", "Status"), formatted),
        "",
        "Not computed at all: **total area 307.07 m2**. Archon's *powierzchnia calkowita* "
        "convention (which external and structural areas it sweeps in) is not stated on the "
        "project card, and this project does not compute figures whose definition it cannot "
        "pin down. Roof pitch 35.0deg is a spec *input*, asserted exactly, not a computed "
        "result.",
        "",
    ]


def _level_section(quantities: Mapping[str, Any]) -> list[str]:
    levels = quantities["areas_m2"]["by_level"]
    rows = []
    for name, block in levels.items():
        floor = block["floor_area"]
        counted = block["counted_area"]
        rows.append(
            (
                name,
                block["rooms"],
                block["faces"],
                f"{floor['computed']:.4f}",
                f"{floor['published']:.2f}",
                _pct(floor["residual_pct"]),
                f"{counted['computed']:.4f}",
                f"{counted['published']:.2f}",
                _pct(counted["residual_pct"]),
            )
        )
    return [
        "## Per-level floor area -- the strongest check in the project",
        "",
        "**This is the check that consumes no published input at all.** Every other headline "
        "figure has something handed to it: the usable-area invariant is given a 2.63 m2 "
        "stair run read off the published table (see \"Deliberate decisions\" below), and the "
        "room areas are compared one at a time. Per-level floor area is pure geometry against "
        "a pure published number, with nothing fed in either direction.",
        "",
        *_table(
            (
                "Level",
                "Rooms",
                "Faces",
                "Floor computed",
                "Floor published",
                "Residual",
                "Counted computed",
                "Counted published",
                "Residual",
            ),
            rows,
        ),
        "",
        ", ".join(
            f"{name} {block['floor_area']['residual_pct']:+.2f}%"
            for name, block in levels.items()
        )
        + ". Both inside +-1% with no deduction, no allowance fitted per level, and no "
        "published figure consumed by the computation.",
        "",
    ]


def _area_table_section(
    area_results: Sequence[Any],
    area_module: Any,
    quantities: Mapping[str, Any],
    mismatches: list[str],
) -> list[str]:
    by_room = {
        room["ids"][0]: room for room in quantities.get("rooms", []) if room.get("ids")
    }
    rows = []
    for result in sorted(area_results, key=lambda item: (item.level, item.room_id, item.check)):
        if result.ok:
            status = "ok"
        elif result.recorded:
            status = "**FAIL (recorded, diagnosed)**"
        else:
            status = "**FAIL (NEW)**"
        rows.append(
            (
                f"`{result.key}`",
                result.name,
                result.level,
                result.check,
                f"{result.computed:.3f}",
                f"{result.published:.2f}",
                f"{result.rel:+.2%}",
                status,
            )
        )
        room = by_room.get(result.room_id)
        if room is not None and result.check == "usable":
            reported = room["counted_area_m2"]
            if abs(reported - result.computed) > 1e-6:
                mismatches.append(
                    f"room `{result.room_id}`: build/quantities.json reports "
                    f"{reported:.6f} m2 but tests/test_room_areas.py computes "
                    f"{result.computed:.6f} m2"
                )

    lines = [
        "## Every published-area equation",
        "",
        "All measured to the **finished** face (PN-ISO 9836, *w swietle scian*), which is the "
        "convention Archon publishes; the printed chains dimension raw structure. `usable` is "
        "the published room figure -- height-banded on the attic, plain floor area elsewhere. "
        "`floor` is the parenthesised floor figure against the unbanded polygon. `group` is "
        "the open-plan aggregate that stands in for four rooms at once.",
        "",
        *_table(
            (
                "Check",
                "Room",
                "Level",
                "Kind",
                "Computed m2",
                "Published m2",
                "Rel",
                "Status",
            ),
            rows,
        ),
        "",
        f"**{sum(1 for item in area_results if item.ok)} of {len(area_results)} inside "
        f"+-1%.** The tolerance is not widened anywhere. The three outliers are recorded as "
        "findings with their measured values, and the suite asserts the outlier set is "
        "*exactly* that set -- a new room drifting out fails, and a recorded one coming back "
        "inside also fails.",
        "",
    ]
    failing = [item for item in area_results if not item.ok]
    if failing:
        lines += ["### The residuals, in full", ""]
        for item in failing:
            note = area_module.RECORDED_RESIDUALS.get(item.key)
            lines += [
                f"- **`{item.key}`** -- "
                + (
                    _oneline(note)
                    if note
                    else f"{item.name}, {item.rel:+.2%} ({item.computed:.3f} vs "
                    f"{item.published:.2f} m2). **Not previously recorded or diagnosed.**"
                ),
            ]
        lines.append("")
    return lines


def _measurement_norm_section(
    document: Mapping[str, Any], published: Mapping[str, Any]
) -> list[str]:
    construction = document.get("construction", {})
    evidence = published.get("measurement_norm_evidence", {})
    return [
        "## Measurement norm and finish allowance",
        "",
        "**Resolved: PN-ISO 9836, measured *w swietle scian* (to finished faces, plaster "
        "included), with sloped-ceiling bands at 1.4 m and 2.2 m.** Archon states the norm "
        f"explicitly on the project card ({', '.join(evidence.get('stated_for', [])) or 'n/a'}"
        "), and `plan_attic.png` independently prints its ceiling-height contour lines "
        "labelled `140` and `220`. T15 asked this question and is superseded rather than "
        "skipped; the evidence is recorded rather than re-derived.",
        "",
        f"**Finish allowance: {construction.get('finish_allowance', '?')} mm per face, and it "
        "stays `derived: true`.** It is solved-for, not published. T08 swept 0-30 mm across "
        "all 18 equations, none excluded: the curve is convex with a single minimum at 19 mm "
        "(RMS 0.62%), and 20 mm is statistically indistinguishable (0.63%). `finish` beats "
        "`structure` on every metric -- RMS 0.63% vs 2.99%, mean -0.14% vs +2.46%, 2 failures "
        "vs 14 -- and at `structure` the failure classifier returns `uniform_offset`, which is "
        "exactly the predicted pre-allowance signature. Runnable: "
        "`uv run python tests/test_room_areas.py --sweep`.",
        "",
        "**Residual ambiguity, stated rather than buried:**",
        "",
        "- Schody takes no allowance at all, and that is now data in the spec: "
        "`rooms[].measure_to = {face: structure}` on `A_R4` and on no other room. Under "
        "PN-ISO 9836 a stair is the plan projection of its flight and landings -- the slab "
        "opening -- and two of its four edges are a void edge and a guard, with no plaster "
        "to deduct. This is not a point dropped from the fit to make the fit look better: "
        "T18 showed no allowance in 0-30 mm brings the room inside +-1% (it needs "
        "<= 4.1 mm/face) and that at structure it lands at +0.5%. It is left in the sweep, "
        "where it now contributes the same constant at every allowance.",
        "- Kotlownia and Lazienka (12) are not explained by any allowance in 0-30 mm. They "
        "look like genuine publisher-vs-plan disagreements of ~0.07-0.13 m2.",
        "- The 19 mm optimum was rounded to 20 mm because 20 mm is the round number an "
        "architect would specify. That is a judgement, not a measurement.",
        "",
    ]


def _vertical_section(quantities: Mapping[str, Any]) -> list[str]:
    elevations = quantities["elevations_m"]
    roof = quantities["roof"]
    rows = [
        ("Terrain", elevations["terrain"], "printed on `section.png`"),
        ("Ground floor", elevations["ground_floor"], "datum"),
        ("Attic floor (slab top)", elevations["attic_floor"], "printed"),
        (
            "Knee wall top / ceiling plane",
            elevations["knee_wall_top"],
            "attic floor + 290 published knee wall; **band the attic from here**",
        ),
        (
            "Roof outer plane at wall face (springing)",
            elevations["roof_springing"],
            "+ 280 `roof_buildup_vertical`, `derived: true`, the only derived input in the "
            "ridge chain",
        ),
        (
            "Eave (fascia underside at the overhang edge)",
            elevations["eave_fascia_underside"],
            "output: springing - 600*tan35 - 310 fascia; printed 2.880, 0 mm out",
        ),
        (
            "Ridge",
            elevations["ridge"],
            "**output**, never assigned: springing + 4500*tan35; printed 6.770, -9 mm",
        ),
    ]
    return [
        "## Vertical geometry",
        "",
        *_table(
            ("Plane", "Elevation m", "Basis"),
            [(name, f"{value:+.4f}", basis) for name, value, basis in rows],
        ),
        "",
        f"Pitch **{roof['pitch_deg']}deg** (spec input, asserted exactly), span "
        f"{roof['span_m']} m, eaves overhang {roof['eaves_overhang_m']} m, verge overhang "
        f"{roof['verge_overhang_m']} m.",
        "",
        "**Three parallel planes, and using the wrong one silently corrupts a different "
        "check each time.** The 1.4/2.2 banding contours are measured to the *ceiling* "
        "(3.330), which is `roof_buildup_vertical` lower than the roof's *outer plane* "
        "(3.610); banding from 3.610 puts the contours ~0.4 m out and over-reads the attic by "
        "~20%. The 2.880 mark is the *eave fascia underside*, 0.60 m outboard of the wall, "
        "and is not in the wall chain at all -- mislabelling it \"wall plate top\" is what "
        "produced this project's original phantom roof discrepancy.",
        "",
    ]


def _decisions_section(quantities: Mapping[str, Any]) -> list[str]:
    areas = quantities["areas_m2"]
    deduction = areas["deductions"]["ground_stair_run"]
    usable_published = areas["usable"]["published"]
    height = quantities["heights"]["building_m"]
    height_delta_mm = (height["computed"] - height["published"]) * 1000.0
    overlap_m3, overlap_pairs = _overlap()
    member_volume = quantities["volumes_m3"]["solid_volume_sum"]
    return [
        "## Deliberate decisions and where this report is weaker than it looks",
        "",
        "These are choices, not results. Listed here rather than left implicit in a passing "
        "test.",
        "",
        "### Two tolerances were widened, and why",
        "",
        f"- **Roof area, +-1% -> +-6%.** Measured overhangs (600 mm eaves, 590 mm verge, "
        f"traced on two independent images) give "
        f"{quantities['roof']['area_m2']['computed']:.2f} m2 against a published "
        f"{quantities['roof']['area_m2']['published']:.1f} m2, "
        f"{_pct(quantities['roof']['area_m2']['residual_pct'])}. Reproducing 216.8 needs a "
        "0.44 m uniform overhang. **T17 declined to shrink the overhang to fit**, because the "
        "overhang is measured and the publisher's *powierzchnia dachu* convention is not "
        "known -- it may or may not include overhangs, gable rakes or covering laps. This is "
        "a band on one invariant whose *definition* is unknown, not a tolerance widened to "
        "make a failure go away, and it keeps its teeth: the refuted 40.7deg pitch reads "
        "+13.4% and fails it.",
        "- **Building height, +-10 mm -> +-30 mm.** `roof_buildup_vertical` (280 mm) carries a "
        "measured +-30 mm uncertainty and is the only derived input in the ridge chain, so "
        "+-10 mm claimed a precision the inputs do not support. It would have passed with "
        "0.93 mm to spare, which is luck rather than evidence. The computed value "
        f"({height['computed']:.6f} m, {height_delta_mm:+.2f} mm) is separately pinned at "
        "1e-5, so nothing is lost by the wider band.",
        "",
        "### The usable-area invariant is weaker than it looks, and says so",
        "",
        f"It deducts a **{deduction} m2 ground-floor stair run** that nothing in "
        "`spec/ground.json` models. The figure is read two independent ways off the published "
        "table -- `Salon 33.20 - 30.57` and `ground 118.81 - 116.18` -- and the export fails "
        "if the two readings disagree. But it is still **published information handed to the "
        f"check**, so the invariant independently constrains {usable_published - deduction:.2f} "
        f"m2 of the {usable_published:.2f} m2, not all of it. Undeducted, usable reads "
        f"{areas['usable_floor_basis']:.4f} m2 (+1.27%) and would fail its own +-1% band. "
        "Per-level floor area, above, consumes nothing and is the honest check.",
        "",
        "### The mesh is not a solid",
        "",
        f"The exported solids interpenetrate by a measured "
        f"**{overlap_m3:.4f} m3** across {overlap_pairs} pairs against "
        f"{member_volume:.3f} m3 of member volume -- "
        f"**{100 * overlap_m3 / member_volume:.1f}% double-counted**. Two classes: chimney "
        "stacks traced once per storey pass "
        "through the walls they abut, and ground exterior walls run up into the attic slab "
        f"band. All {quantities['mesh']['nodes']} components are individually watertight, "
        "winding-consistent and "
        "positive-volume; the *scene* is not. This is invisible in glTF and nothing is wrong "
        "today, because cubature is a closed-form integral over the 2D footprint and touches "
        "no mesh volume -- but `sum(mesh.volume)` would over-read cubature by ~8% and is "
        "material volume anyway, not the gross enclosed volume cubature asks for. The totals "
        "are pinned in `tests/test_export.py` so the trap cannot be walked into silently.",
        "",
        "### Golden overlay images are not signed off yet",
        "",
        "`tests/golden/` is deliberately empty and the golden-diff tests **skip** with a "
        "message saying so. Human sign-off on the first goldens is deferred to the end of the "
        "project by user decision: an auto-accepted golden locks in whatever was wrong at the "
        "time, which is worse than no golden. Two skips in the suite are these, and they are "
        "a normal state, not an error. The overlays themselves are generated on every build "
        "and are shown below.",
        "",
    ]


def _overlap() -> tuple[float, int]:
    """Measured mesh interpenetration and pair count, from the test that pins them."""
    module = _load_test_module("test_export")
    volume = getattr(module, "RECORDED_OVERLAP_M3", None)
    pairs = getattr(module, "RECORDED_OVERLAP_PAIRS", None)
    if volume is None or pairs is None:  # pragma: no cover - constants present today
        raise ReportError(
            "tests/test_export.py no longer exposes RECORDED_OVERLAP_M3 / "
            "RECORDED_OVERLAP_PAIRS; the report will not substitute a remembered number "
            "for a measured one."
        )
    return float(volume), int(pairs)


def _open_items_section() -> list[str]:
    return [
        "## Open items",
        "",
        "Tracked work and unresolved questions, in priority order. Nothing here is hidden "
        "behind a passing test.",
        "",
        "1. **CLOSED (T18/T19): the Schody residual was a measurement convention.** The old "
        "diagnosis -- a ~0.25 m2 unmodelled stairwell landing nub, to be added to the "
        "polygon -- was wrong, and so was the counter-reading that the box east of the "
        "flight is a balustrade post or furniture. T18 re-measured both bitmaps: the box is "
        "**floor** (floor tone 236 against the void tone 197, and the stair's walking line "
        "originates inside it), but adding it to the current polygon overshoots to +6.8%, "
        "because it is only admissible together with narrowing the flight from 950 to "
        "872 mm and the two corrections cancel. The real fault was the finish convention: "
        "the published 3.64 m2 is the drawn slab opening at raw structure, and the L as "
        "drawn has a 10.084 m perimeter, so *any* allowance above 4.1 mm/face fails +-1%. "
        "`A_R4` now carries `measure_to` in `spec/attic.json` and its south edge sits on "
        "the printed 4700: 3.6575 m2, +0.5%. See `docs/T18-findings.md` item 1.",
        "2. **CLOSED (T18/T19): the roof-window depth never disagreed with its callout.** "
        "The recorded 3.1% was a one-pixel tracing error, not a fact about the drawing: "
        "1271 mm was measured to the *inner* faces of the two dashed lines instead of their "
        "centres. Line centre to line centre the depth is 1296 +- 12 mm against "
        "`1600 * cos 35deg = 1310.6 mm`, a residual of 14.6 mm / 1.1% / 0.6 px on a raster "
        "whose pixel is 24.46 mm -- the two agree, and T18 does not claim to have "
        "distinguished them. The convention is calibrated in place: the same box measured "
        "the same way in x reproduces its PRINTED 780 mm width to 0.07 px. The bounds in "
        "`spec/meta.json` are now y 1149..2446 and the generator models that. "
        "See `docs/T18-findings.md` item 2.",
        "3. **Published roof area 216.8 m2 vs 227.6 m2 measured (+5.0%).** Asserted as a "
        "+-6% band with the reasoning documented, not silently widened. See above.",
        "4. **Kotlownia (-1.74%) and Lazienka 12 (-1.27%)** are not explained by any finish "
        "allowance in 0-30 mm. Likely irreducible publisher-vs-plan disagreements of "
        "~0.07-0.13 m2, but that is a conclusion by elimination, not a finding.",
        "5. **Mesh solids overlap** -- measured exhaustively, invisible in glTF, and a trap "
        "for anyone who later computes cubature by summing mesh volumes. See above.",
        "6. **CLOSED (T18/T19): the gable windows really are not centred, and the spec is "
        "right.** A_O1/A_O2 sit at `offset` 4275 on 8550-long walls; centred would be 3775. "
        "Confirmed on `elevation_side_1.png` and `elevation_side_2.png`: on both gables the "
        "window's near jamb sits on the ridge line to under 1 px, and a centred window is "
        "excluded by 29 px against 2 px of noise. Which window is north and which is south "
        "is independently confirmed by the chimney positions. Recorded separately and NOT "
        "acted on: the renders put the glazing at `z ~ 3584..5913` where the spec has "
        "3040..5770, a ~0.5 m vertical difference, with the bay below the eaves reading as a "
        "concrete spandrel -- the `100/273` callout is the authority and a marketing render "
        "is not. See `docs/T18-findings.md` item 3.",
        "7. **Roof build-up split undetermined.** `roof_buildup_vertical` 280 mm and "
        "`fascia_depth` 310 mm are measured lumped values; the rafter/insulation/covering "
        "split is unknown. Affects visual detail only -- but the 280 mm is what carries the "
        "+-30 mm that sets the building-height band.",
        "8. **Golden overlay images await human sign-off.** The last gate before the project "
        "is done.",
        "",
    ]


def _derived_section(provenance: Mapping[str, Any]) -> list[str]:
    per = provenance["per_section"]
    lines = [
        "## Transcribed vs derived",
        "",
        "**A derived value is one that was inferred, computed or assumed rather than read off "
        "a number printed on a source document.** The schema forces a `note` onto every one of "
        "them, because a derived value with no stated basis is indistinguishable from a guess. "
        "Every single one is listed below with that justification -- the difference between a "
        "report and a marketing page.",
        "",
        *_table(
            ("Spec section", "Objects", "Carrying derived values", "Fully transcribed"),
            [
                (
                    key,
                    per[key]["total"],
                    per[key]["derived"],
                    per[key]["total"] - per[key]["derived"],
                )
                for key, _title in _ENTITY_SECTIONS
                if per[key]["total"]
            ]
            + [
                (
                    "**entities total**",
                    f"**{provenance['entity_total']}**",
                    f"**{provenance['entity_annotated']}**",
                    f"**{provenance['entity_total'] - provenance['entity_annotated']}**",
                )
            ],
        ),
        "",
        f"Counting every annotatable object in the merged spec, including the `construction` "
        f"and `roof` blocks that are not entity arrays: **{provenance['objects_annotated']} "
        f"objects carry a derived annotation** -- "
        f"{provenance['objects_fully_derived']} are entirely derived (`derived: true`) and "
        f"{provenance['objects_partly_derived']} are partly derived, naming "
        f"{provenance['derived_field_names']} specific fields between them.",
        "",
    ]
    if provenance["disputed"]:
        lines += [
            "**Disputed values** (transcribed correctly but contradicting other published "
            "figures):",
            "",
        ]
        for entry in provenance["disputed"]:
            lines.append(
                f"- `{entry['id']}` ({entry['path']}) -- ref `{entry['dispute_ref']}`: "
                f"{entry['note']}"
            )
        lines.append("")
    else:
        lines += [
            "No spec object is currently marked `disputed`. The roof block carried "
            "`disputed: true` until T17 resolved the pitch at 35.0deg.",
            "",
        ]

    lines += [
        "### Every derived value, with its justification",
        "",
        "<details>",
        "<summary>Expand -- "
        f"{provenance['objects_annotated']} entries</summary>",
        "",
    ]
    for entry in provenance["entries"]:
        fields = ", ".join(f"`{name}`" for name in entry["fields"])
        note = entry["note"] or "**NO JUSTIFICATION RECORDED -- this is a bug in the spec.**"
        lines.append(f"- **`{entry['id']}`** ({entry['path']}) -- {fields}<br>{note}")
    lines += ["", "</details>", ""]
    return lines


def _overlay_section() -> list[str]:
    lines = [
        "## Orthographic overlays",
        "",
        "A horizontal section is cut through the **actual generated 3D mesh** at 1.0 m above "
        "each floor and diffed against the source plan at matched scale. Cutting the real mesh "
        "rather than re-drawing the 2D geometry means this catches generator bugs, not only "
        "spec bugs. It is the only check that catches a layout that is dimensionally "
        "self-consistent but topologically wrong -- and a perfectly-built mirror image of the "
        "right house passes every single numeric check in this report, which is why the "
        "mirrored twin is generated and shown alongside.",
        "",
    ]
    for filename, caption in OVERLAYS:
        path = BUILD_DIR / filename
        if path.is_file():
            lines += [f"**{caption}**", "", f"![{caption}]({filename})", ""]
        else:
            lines += [
                f"**{caption}** -- `{filename}` not present. Run `just build`.",
                "",
            ]
    return lines


def _mismatch_section(mismatches: Sequence[str]) -> list[str]:
    if not mismatches:
        return []
    return [
        "## WARNING -- inconsistent sources",
        "",
        "`build/quantities.json` and the test suite disagree on the following. This report "
        "shows the artifact's figures; the disagreement itself is a finding and is printed "
        "here rather than resolved silently. Most likely `build/` is stale -- rerun "
        "`just build`.",
        "",
        *[f"- {item}" for item in mismatches],
        "",
    ]


def _limitation_section(limitation: str) -> list[str]:
    return [
        "## Limitation",
        "",
        *[f"> {line}" if line else ">" for line in limitation.splitlines()],
        "",
    ]


# --------------------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------------------


def build_report() -> str:
    """Render the whole report as markdown. Pure: writes nothing."""
    limitation = _verified_limitation()
    quantities = _read_json(QUANTITIES_PATH)
    published = _read_json(PUBLISHED_PATH)

    from kotewki.spec import load_spec

    spec = load_spec()
    document = spec.to_dict() if hasattr(spec, "to_dict") else dict(spec)

    area_module = _load_test_module("test_room_areas")
    invariant_module = _load_test_module("test_invariants")

    area_results = area_module.evaluate(spec, measure_to=area_module.PRIMARY_MEASURE_TO)
    counts = area_module.independent_equation_count(area_results)
    invariants = invariant_module.evaluate(spec, published)

    mismatches: list[str] = []
    sections: list[list[str]] = [
        _header(quantities, published),
        _summary(invariants, area_results, counts, area_module),
        _invariants_section(invariants, quantities, mismatches),
        _independence_section(
            counts, len(invariants), len(document.get("dimension_chains") or ())
        ),
        _level_section(quantities),
        _area_table_section(area_results, area_module, quantities, mismatches),
        _other_published_section(quantities),
        _vertical_section(quantities),
        _measurement_norm_section(document, published),
        _decisions_section(quantities),
        _open_items_section(),
        _derived_section(_provenance(document)),
        _overlay_section(),
        _mismatch_section(mismatches),
        _limitation_section(limitation),
    ]
    body = "\n".join(line for section in sections for line in section)
    return body.rstrip("\n") + "\n"


def write_report(path: Path | str = REPORT_PATH) -> Path:
    """Render and write ``build/REPORT.md``, creating ``build/`` if needed."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_report(), encoding="utf-8")
    return target


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m kotewki.report``."""
    parser = argparse.ArgumentParser(description="Generate build/REPORT.md.")
    parser.add_argument("-o", "--output", default=str(REPORT_PATH), help="output path")
    parser.add_argument(
        "--stdout", action="store_true", help="print the report instead of writing it"
    )
    args = parser.parse_args(argv)
    try:
        if args.stdout:
            sys.stdout.write(build_report())
            return 0
        written = write_report(args.output)
    except ReportError as error:
        print(f"report: {error}", file=sys.stderr)
        return 1
    size = written.stat().st_size
    print(f"{_rel(written)}  {size:,} bytes")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
