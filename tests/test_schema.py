"""Self-tests for spec/schema.json and the kotewki.spec loader (T02).

These do not check the real house. They check that **the contract holds**: that a valid
spec loads, and that each way of writing an invalid one is rejected loudly rather than
accepted silently. Every other task in the project reads and writes against this schema,
so a hole here is a hole everywhere.

The tests build spec files in a tmp directory rather than reading spec/*.json, so they keep
passing while T04 and T05 transcribe. One test does load the real spec/ directory, to catch
spec/meta.json drifting out of conformance with its own schema.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from kotewki.spec import (
    CM_TO_MM,
    SpecError,
    SpecValidationError,
    load_spec,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_SPEC_DIR = REPO_ROOT / "spec"


# --------------------------------------------------------------------------------------
# Fixtures: a minimal but genuinely valid spec — 4 walls, 1 room, 1 opening, 1 chain.
# --------------------------------------------------------------------------------------


def minimal_meta() -> dict[str, Any]:
    return {
        "meta": {
            "schema_version": "1.0.0",
            "source_url": "https://example.invalid/projekt",
            "variant": "E",
            "transcribed_by": "test",
            "date": "2026-08-30",
        },
        "levels": [
            {"id": "ground", "name": "PARTER", "elevation": 0, "ceiling_height": 2700},
            {"id": "attic", "name": "PODDASZE", "elevation": 3040, "ceiling_height": 2730},
        ],
        "construction": {
            "exterior_wall": {
                "thickness": 465,
                "layers": [
                    {"material": "Porotherm 25", "thickness": 250},
                    {"material": "EPS", "thickness": 200},
                    {"material": "tynk", "thickness": 15},
                ],
            },
            "ceiling": {
                "thickness": 340,
                "layers": [{"material": "żelbet", "thickness": 340}],
            },
            "knee_wall_height": 290,
            "finish_allowance": 20,
        },
        "section_elevations": {
            "terrain": -320,
            "ground_floor": 0,
            "eave_fascia_underside": 2880,
            "attic_floor": 3040,
            "ridge": 6770,
            "source_image": "data/source/section.png",
        },
        "roof": {"type": "gable", "pitch_deg": 35.0, "eaves_overhang": 400},
    }


def minimal_ground() -> dict[str, Any]:
    """A 4000 x 3000 mm rectangular room, walls given as centrelines."""
    corners = [(0, 0), (4000, 0), (4000, 3000), (0, 3000)]
    walls = [
        {
            "id": f"G_W{index + 1}",
            "level": "ground",
            "start": list(corners[index]),
            "end": list(corners[(index + 1) % 4]),
            "thickness": 465,
            "type": "exterior",
        }
        for index in range(4)
    ]
    return {
        "walls": walls,
        "openings": [
            {
                "id": "G_O1",
                "wall": "G_W1",
                "offset": 1000,
                "width": 1400,
                "height": 1500,
                "sill": 850,
                "kind": "window",
                "swing": "none",
            }
        ],
        "rooms": [
            {
                "id": "G_R1",
                "published_id": 3,
                "name": "Pokój",
                "level": "ground",
                "boundary": ["G_W1", "G_W2", "G_W3", "G_W4"],
                "published_area": 11.99,
                "area_groups": ["usable", "net"],
            }
        ],
        "dimension_chains": [
            {
                "id": "G_C1",
                "level": "ground",
                "axis": "x",
                "segments_cm": [470, 224, 1016],
                "total_cm": 1710,
                "source_image": "data/source/plan_ground.png",
            }
        ],
    }


def minimal_attic() -> dict[str, Any]:
    return {"walls": [], "openings": [], "rooms": [], "dimension_chains": []}


def write_spec(
    directory: Path,
    *,
    meta: dict[str, Any] | None = None,
    ground: dict[str, Any] | None = None,
    attic: dict[str, Any] | None = None,
) -> Path:
    """Write the three spec files into ``directory``. Pass ``None`` to omit a file."""
    for name, document in (("meta", meta), ("ground", ground), ("attic", attic)):
        if document is not None:
            (directory / f"{name}.json").write_text(
                json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    return directory


@pytest.fixture
def spec_dir(tmp_path: Path) -> Path:
    return write_spec(
        tmp_path, meta=minimal_meta(), ground=minimal_ground(), attic=minimal_attic()
    )


def expect_invalid(directory: Path) -> SpecValidationError:
    with pytest.raises(SpecValidationError) as excinfo:
        load_spec(directory)
    return excinfo.value


def messages(error: SpecValidationError) -> str:
    return "\n".join(error.errors)


# --------------------------------------------------------------------------------------
# Positive cases
# --------------------------------------------------------------------------------------


def test_minimal_spec_loads(spec_dir: Path) -> None:
    spec = load_spec(spec_dir)
    assert len(spec.walls) == 4
    assert len(spec.rooms) == 1
    assert spec.rooms[0].published_area == 11.99
    assert spec.wall_by_id["G_W1"].thickness == 465
    assert spec.level_by_id["attic"].elevation == 3040


def test_spec_is_also_a_plain_mapping(spec_dir: Path) -> None:
    """TESTS.md and T06 index the spec as a dict; that access must keep working."""
    spec = load_spec(spec_dir)
    for chain in spec["dimension_chains"]:
        assert sum(chain["segments_cm"]) == chain["total_cm"]
    assert set(spec.keys()) >= {"meta", "levels", "walls", "rooms", "dimension_chains"}


def test_real_spec_directory_loads() -> None:
    """spec/meta.json must always satisfy its own schema, even before T04/T05 land."""
    spec = load_spec(REAL_SPEC_DIR)
    assert {level.id for level in spec.levels} == {"ground", "attic"}
    assert spec.roof.pitch_deg == 35.0
    # T17 resolved the roof (docs/roof-resolution.md): the published 35° is correct, and the
    # apparent discrepancy was our own mislabelling of the +2.88 section mark. So the dispute
    # flags are legitimately gone — but pitch_deg stays *transcribed*, never derived.
    assert not spec.roof.is_disputed("pitch_deg"), "T17 resolved this; see docs/roof-resolution.md"
    assert not spec.roof.is_derived("pitch_deg"), "35° is transcribed, not derived"
    assert spec.roof.is_derived("eaves_overhang"), "the overhang is measured, not published"
    assert spec.roof.springing == "knee_wall_top"
    assert spec.construction["knee_wall_height"] == 290
    assert spec.section_elevations["ridge"] - spec.section_elevations["terrain"] == 7090

    # The roof must remain reconstructible from inputs alone, so that T09's ridge and
    # building-height assertions stay genuine checks rather than tautologies. This is
    # T17's formulation (docs/roof-resolution.md §8): the ridge is an OUTPUT, and
    # roof_buildup_vertical is the single derived input in the chain.
    import math

    roof = spec["roof"]  # raw mapping: the typed Roof view predates T17's added fields
    springing = (
        spec.level_by_id["attic"].elevation
        + spec.construction["knee_wall_height"]
        + roof["roof_buildup_vertical"]
    )
    tan = math.tan(math.radians(roof["pitch_deg"]))
    ridge = springing + (9000 / 2) * tan
    eave = springing - roof["eaves_overhang"] * tan - roof["fascia_depth"]

    assert abs(ridge - spec.section_elevations["ridge"]) <= 30, f"ridge {ridge:.0f} mm"
    assert abs(eave - spec.section_elevations["eave_fascia_underside"]) <= 30, f"eave {eave:.0f} mm"
    assert abs((ridge - spec.section_elevations["terrain"]) - 7090) <= 30, "building height"


def test_the_measurement_carve_out_is_exactly_one_room_and_it_says_why() -> None:
    """``rooms[].measure_to`` is a carve-out, so it is pinned to the one room that has it.

    T19 gave the geometry kernel a per-room measurement override so that Schody -- a slab
    opening, not a room *w swietle scian* -- could stop taking the 20 mm/face finish
    allowance without a room id being branched on inside ``geometry.py``. An escape hatch
    like that earns its keep only while it stays one room deep and stays justified, so
    both are asserted rather than trusted: a second room quietly acquiring an exemption is
    the failure mode, and it would otherwise show up only as an area check getting easier.
    """
    spec = load_spec(REAL_SPEC_DIR)
    overridden = {room.id: room for room in spec.rooms if room.measure_to is not None}

    assert set(overridden) == {"A_R4"}, (
        f"rooms[].measure_to is a documented one-room carve-out for Schody; "
        f"{sorted(overridden)} carry it. See docs/T18-findings.md item 1 before adding "
        f"another -- and if a second room genuinely needs one, it needs its own evidence."
    )
    schody = overridden["A_R4"]
    assert schody.name == "Schody"
    assert schody.measure_to is not None
    assert schody.measure_to.face == "structure"
    assert schody.measure_to.extends_under == ("A_W7",)
    assert "PN-ISO 9836" in (schody.note or ""), "the exemption must be argued in the note"


def test_measure_to_rejects_a_face_that_is_not_a_measurement_face(tmp_path: Path) -> None:
    """The face is a closed enum: a typo must not silently mean 'model default'."""
    ground = minimal_ground()
    ground["rooms"][0]["measure_to"] = {"face": "plaster"}
    write_spec(tmp_path, meta=minimal_meta(), ground=ground, attic=minimal_attic())
    assert "measure_to" in messages(expect_invalid(tmp_path))


def test_measure_to_may_not_extend_a_room_under_a_wall_that_does_not_bound_it(
    tmp_path: Path,
) -> None:
    """``extends_under`` hands a wall's footprint to a room; only its own walls qualify.

    Otherwise the field is a licence to annex floor area from a room on the far side of
    the plan, and the union-vs-footprint check would not notice -- a union does not care
    that two rooms claim the same square metre.
    """
    ground = minimal_ground()
    ground["walls"].append(
        {
            "id": "G_W5",
            "level": "ground",
            "start": [0, 1500],
            "end": [4000, 1500],
            "thickness": 120,
            "type": "partition",
        }
    )
    ground["rooms"][0]["measure_to"] = {"face": "structure", "extends_under": ["G_W5"]}
    write_spec(tmp_path, meta=minimal_meta(), ground=ground, attic=minimal_attic())
    assert "extends_under" in messages(expect_invalid(tmp_path))


def test_centimetre_fields_convert_in_exactly_one_place(spec_dir: Path) -> None:
    chain = load_spec(spec_dir).dimension_chains[0]
    assert CM_TO_MM == 10
    assert chain.total_cm == 1710
    assert chain.total_mm == 17100
    assert chain.segments_mm == (4700, 2240, 10160)
    assert chain.closes and chain.delta_cm == 0


def test_missing_level_file_warns_but_does_not_crash(tmp_path: Path) -> None:
    """T06–T14 build against fixtures before transcription lands."""
    write_spec(tmp_path, meta=minimal_meta())
    with pytest.warns(UserWarning, match="ground.json not found"):
        spec = load_spec(tmp_path)
    assert spec.walls == () and spec.rooms == ()
    assert len(spec.levels) == 2


def test_missing_level_file_can_be_made_fatal(tmp_path: Path) -> None:
    write_spec(tmp_path, meta=minimal_meta())
    with pytest.raises(SpecError):
        load_spec(tmp_path, require_all_files=True)


def test_missing_meta_is_always_fatal(tmp_path: Path) -> None:
    write_spec(tmp_path, ground=minimal_ground())
    with pytest.raises(SpecError, match="meta.json"):
        load_spec(tmp_path)


# --------------------------------------------------------------------------------------
# The five required negative cases
# --------------------------------------------------------------------------------------


def test_negative_float_length_is_rejected(tmp_path: Path) -> None:
    """A fractional millimetre value is caught by the schema's integer type."""
    ground = minimal_ground()
    ground["walls"][0]["thickness"] = 250.5
    write_spec(tmp_path, meta=minimal_meta(), ground=ground, attic=minimal_attic())
    assert "thickness" in messages(expect_invalid(tmp_path))


def test_negative_integral_float_length_is_also_rejected(tmp_path: Path) -> None:
    """The hole the schema alone cannot close.

    JSON Schema's ``"type": "integer"`` accepts ``250.0``, so ``"thickness": 250.0`` would
    validate. The loader's float sweep rejects it: a float where a millimetre integer
    belongs usually means someone typed metres or did arithmetic in the spec.
    """
    ground = minimal_ground()
    ground["walls"][0]["thickness"] = 465.0
    write_spec(tmp_path, meta=minimal_meta(), ground=ground, attic=minimal_attic())
    text = messages(expect_invalid(tmp_path))
    assert "float value 465.0" in text
    assert "INTEGER" in text


def test_negative_room_without_published_area_is_rejected(tmp_path: Path) -> None:
    """A room with no published area cannot be checked by T08, so it cannot exist."""
    ground = minimal_ground()
    del ground["rooms"][0]["published_area"]
    write_spec(tmp_path, meta=minimal_meta(), ground=ground, attic=minimal_attic())
    assert "published_area" in messages(expect_invalid(tmp_path))


def test_negative_opening_on_nonexistent_wall_is_rejected(tmp_path: Path) -> None:
    ground = minimal_ground()
    ground["openings"][0]["wall"] = "G_W99"
    write_spec(tmp_path, meta=minimal_meta(), ground=ground, attic=minimal_attic())
    text = messages(expect_invalid(tmp_path))
    assert "G_O1" in text and "G_W99" in text


def test_negative_duplicate_id_across_ground_and_attic_is_rejected(tmp_path: Path) -> None:
    """The merge must assert uniqueness, not let the last file written win."""
    attic = minimal_attic()
    attic["walls"] = [
        {
            "id": "G_W1",  # belongs to ground.json — collides on merge
            "level": "attic",
            "start": [0, 0],
            "end": [4000, 0],
            "thickness": 465,
            "type": "exterior",
        }
    ]
    write_spec(tmp_path, meta=minimal_meta(), ground=minimal_ground(), attic=attic)
    text = messages(expect_invalid(tmp_path))
    # Caught twice over, and both are reported: the global uniqueness assertion names the
    # collision, and the per-file id-prefix rule names how it was possible at all.
    assert "duplicate id 'G_W1'" in text
    assert "ground.json:walls" in text and "attic.json:walls" in text
    assert "must start with 'A_'" in text


def test_negative_duplicate_id_within_a_file_is_rejected(tmp_path: Path) -> None:
    """Global uniqueness on its own, with the prefix rule satisfied."""
    ground = minimal_ground()
    ground["walls"][1]["id"] = "G_W1"
    write_spec(tmp_path, meta=minimal_meta(), ground=ground, attic=minimal_attic())
    assert "duplicate id 'G_W1'" in messages(expect_invalid(tmp_path))


def test_negative_duplicate_id_across_collections_is_rejected(tmp_path: Path) -> None:
    """A wall and a room may not share an id either — ids are unique spec-wide."""
    ground = minimal_ground()
    ground["rooms"][0]["id"] = "G_W1"
    write_spec(tmp_path, meta=minimal_meta(), ground=ground, attic=minimal_attic())
    assert "duplicate id 'G_W1'" in messages(expect_invalid(tmp_path))


def test_negative_chain_without_cm_suffix_is_rejected(tmp_path: Path) -> None:
    """The ``_cm`` suffix is mandatory: unsuffixed fields would read as millimetres."""
    ground = minimal_ground()
    chain = ground["dimension_chains"][0]
    chain["segments"] = chain.pop("segments_cm")
    chain["total"] = chain.pop("total_cm")
    write_spec(tmp_path, meta=minimal_meta(), ground=ground, attic=minimal_attic())
    text = messages(expect_invalid(tmp_path))
    assert "segments_cm" in text and "total_cm" in text
    assert "'segments'" in text or "'total'" in text  # additionalProperties rejects them


def test_chain_written_in_millimetres_is_not_caught_here_and_that_is_documented(
    tmp_path: Path,
) -> None:
    """Writing millimetres into a ``_cm`` field — i.e. doing the ×10 during transcription.

    This is the one ``_cm`` misuse the schema and loader CANNOT catch: 17100 =
    4700 + 2240 + 10160 closes perfectly and every value is a valid positive integer. The
    loader applies ×10 unconditionally, which is the design; the defence is T06's magnitude
    assertion (a 171 m building is out of the 5–50 m band). This test pins that boundary so
    nobody later assumes the loader covers it.
    """
    ground = minimal_ground()
    chain = ground["dimension_chains"][0]
    chain["segments_cm"] = [4700, 2240, 10160]
    chain["total_cm"] = 17100
    write_spec(tmp_path, meta=minimal_meta(), ground=ground, attic=minimal_attic())
    spec = load_spec(tmp_path)
    loaded = spec.dimension_chains[0]
    assert loaded.closes, "closure alone cannot catch a ×10 — this is T06's magnitude check"
    assert loaded.total_mm == 171000, "the loader applies ×10 unconditionally, as designed"


def test_negative_chain_with_float_centimetres_is_rejected(tmp_path: Path) -> None:
    ground = minimal_ground()
    ground["dimension_chains"][0]["segments_cm"] = [470.0, 224, 1016]
    write_spec(tmp_path, meta=minimal_meta(), ground=ground, attic=minimal_attic())
    assert "float value 470.0" in messages(expect_invalid(tmp_path))


def test_negative_chain_without_source_image_is_rejected(tmp_path: Path) -> None:
    """Every chain must be traceable to the plan it came from, for T15."""
    ground = minimal_ground()
    del ground["dimension_chains"][0]["source_image"]
    write_spec(tmp_path, meta=minimal_meta(), ground=ground, attic=minimal_attic())
    assert "source_image" in messages(expect_invalid(tmp_path))


# --------------------------------------------------------------------------------------
# The rest of the contract
# --------------------------------------------------------------------------------------


def test_unknown_field_is_rejected(tmp_path: Path) -> None:
    """additionalProperties: false is the real guard against an ambiguous unit sneaking in."""
    ground = minimal_ground()
    ground["walls"][0]["width_m"] = 0.465
    write_spec(tmp_path, meta=minimal_meta(), ground=ground, attic=minimal_attic())
    assert "width_m" in messages(expect_invalid(tmp_path))


def test_derived_requires_a_note(tmp_path: Path) -> None:
    ground = minimal_ground()
    ground["openings"][0]["derived"] = True
    write_spec(tmp_path, meta=minimal_meta(), ground=ground, attic=minimal_attic())
    assert "note" in messages(expect_invalid(tmp_path))


def test_disputed_requires_a_note_and_a_ref(tmp_path: Path) -> None:
    meta = minimal_meta()
    meta["roof"]["disputed"] = True
    write_spec(tmp_path, meta=meta, ground=minimal_ground(), attic=minimal_attic())
    text = messages(expect_invalid(tmp_path))
    assert "note" in text and "dispute_ref" in text


def test_derived_and_disputed_are_independent(tmp_path: Path) -> None:
    """A faithfully transcribed value can still be disputed — that is roof.pitch_deg."""
    meta = minimal_meta()
    meta["roof"].update(
        {
            "disputed_fields": ["pitch_deg"],
            "dispute_ref": "T17",
            "note": "published 35° does not reconcile with the section's ridge",
        }
    )
    write_spec(tmp_path, meta=meta, ground=minimal_ground(), attic=minimal_attic())
    roof = load_spec(tmp_path).roof
    assert roof.is_disputed("pitch_deg") and not roof.is_derived("pitch_deg")


def test_derived_fields_must_name_a_real_field(tmp_path: Path) -> None:
    """A typo'd annotation silently marks nothing, presenting a guess as source fact."""
    meta = minimal_meta()
    meta["roof"]["derived_fields"] = ["eave_overhang"]  # missing the 's'
    meta["roof"]["note"] = "solved against the published roof area"
    write_spec(tmp_path, meta=meta, ground=minimal_ground(), attic=minimal_attic())
    assert "eave_overhang" in messages(expect_invalid(tmp_path))


def test_wall_layers_must_sum_to_thickness(tmp_path: Path) -> None:
    ground = minimal_ground()
    ground["walls"][0]["layers"] = [
        {"material": "Porotherm 25", "thickness": 250},
        {"material": "EPS", "thickness": 200},
    ]  # 450, but thickness says 465
    write_spec(tmp_path, meta=minimal_meta(), ground=ground, attic=minimal_attic())
    text = messages(expect_invalid(tmp_path))
    assert "450" in text and "465" in text


def test_wrong_level_in_a_level_file_is_rejected(tmp_path: Path) -> None:
    ground = minimal_ground()
    ground["walls"][0]["level"] = "attic"
    write_spec(tmp_path, meta=minimal_meta(), ground=ground, attic=minimal_attic())
    assert "must be on level 'ground'" in messages(expect_invalid(tmp_path))


def test_unknown_level_is_rejected(tmp_path: Path) -> None:
    ground = minimal_ground()
    for wall in ground["walls"]:
        wall["level"] = "basement"
        wall["id"] = wall["id"]
    write_spec(tmp_path, meta=minimal_meta(), ground=ground, attic=minimal_attic())
    assert "basement" in messages(expect_invalid(tmp_path))


def test_cross_file_key_is_rejected(tmp_path: Path) -> None:
    """T04 must not be able to redefine levels inside ground.json."""
    ground = minimal_ground()
    ground["levels"] = [{"id": "ground", "name": "X", "elevation": 0, "ceiling_height": 9999}]
    write_spec(tmp_path, meta=minimal_meta(), ground=ground, attic=minimal_attic())
    text = messages(expect_invalid(tmp_path))
    assert "does not belong in this file" in text and "meta.json" in text


def test_room_boundary_must_reference_walls_on_the_same_level(tmp_path: Path) -> None:
    attic = minimal_attic()
    attic["walls"] = [
        {
            "id": "A_W1",
            "level": "attic",
            "start": [0, 0],
            "end": [4000, 0],
            "thickness": 250,
            "type": "partition",
        }
    ]
    ground = minimal_ground()
    ground["rooms"][0]["boundary"] = ["G_W1", "G_W2", "A_W1"]
    write_spec(tmp_path, meta=minimal_meta(), ground=ground, attic=attic)
    assert "is on level 'attic'" in messages(expect_invalid(tmp_path))


def test_door_must_declare_its_swing(tmp_path: Path) -> None:
    ground = minimal_ground()
    opening = ground["openings"][0]
    opening["kind"] = "door"
    opening["sill"] = 0
    del opening["swing"]
    write_spec(tmp_path, meta=minimal_meta(), ground=ground, attic=minimal_attic())
    assert "swing" in messages(expect_invalid(tmp_path))


def test_attic_and_boiler_rooms_are_not_also_net_area(tmp_path: Path) -> None:
    """Encodes README's reconciliation: net area excludes the attic and the boiler room."""
    ground = minimal_ground()
    ground["rooms"][0]["area_groups"] = ["usable", "net", "boiler"]
    write_spec(tmp_path, meta=minimal_meta(), ground=ground, attic=minimal_attic())
    assert "powierzchnia netto" in messages(expect_invalid(tmp_path))


def test_area_groups_drive_the_stairs_inclusion_rule(tmp_path: Path) -> None:
    attic = minimal_attic()
    attic["walls"] = [
        {
            "id": f"A_W{i + 1}",
            "level": "attic",
            "start": [0, 0],
            "end": [2000, 0],
            "thickness": 250,
            "type": "partition",
        }
        for i in range(3)
    ]
    attic["rooms"] = [
        {
            "id": "A_R4",
            "published_id": 4,
            "name": "Schody",
            "level": "attic",
            "boundary": ["A_W1", "A_W2", "A_W3"],
            "published_area": 3.64,
            "area_groups": ["net"],
        }
    ]
    write_spec(tmp_path, meta=minimal_meta(), ground=minimal_ground(), attic=attic)
    stairs = load_spec(tmp_path).room_by_id["A_R4"]
    assert stairs.in_net_area and not stairs.in_usable_area


def test_zero_length_wall_is_rejected(tmp_path: Path) -> None:
    ground = minimal_ground()
    ground["walls"][0]["end"] = list(ground["walls"][0]["start"])
    write_spec(tmp_path, meta=minimal_meta(), ground=ground, attic=minimal_attic())
    assert "zero-length" in messages(expect_invalid(tmp_path))


def test_ground_floor_datum_is_pinned_to_zero(tmp_path: Path) -> None:
    meta = minimal_meta()
    meta["section_elevations"]["ground_floor"] = 100
    write_spec(tmp_path, meta=meta, ground=minimal_ground(), attic=minimal_attic())
    assert "ground_floor" in messages(expect_invalid(tmp_path))


def test_all_errors_are_reported_at_once(tmp_path: Path) -> None:
    """Fixing transcription one error per run is miserable; report the whole batch."""
    ground = minimal_ground()
    ground["walls"][0]["thickness"] = 465.0
    ground["walls"][1]["thickness"] = 465.0
    del ground["rooms"][0]["published_area"]
    write_spec(tmp_path, meta=minimal_meta(), ground=ground, attic=minimal_attic())
    error = expect_invalid(tmp_path)
    assert len(error.errors) >= 3


def test_schema_file_is_valid_json_schema() -> None:
    from jsonschema import Draft202012Validator

    schema = json.loads((REAL_SPEC_DIR / "schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)


def test_level_files_carry_only_level_collections() -> None:
    """T04 and T05 get a file to fill, not a schema to re-infer.

    The point of this check is that a level file contains *only* per-level collections —
    no stray top-level keys, and nothing that belongs in meta.json (levels, roof,
    construction, section_elevations). It is not a check that the files stay empty.
    """
    required = {"walls", "openings", "rooms", "dimension_chains"}
    # slab_openings is optional: only the attic has a floor void and a stairwell.
    optional = {"slab_openings"}
    for name in ("ground.json", "attic.json"):
        document = json.loads((REAL_SPEC_DIR / name).read_text(encoding="utf-8"))
        keys = set(document) - {"_comment"}
        assert required <= keys, f"{name} is missing {required - keys}"
        unexpected = keys - required - optional
        assert not unexpected, f"{name} has unexpected keys {unexpected}"


def test_fixture_helpers_are_not_accidentally_shared(spec_dir: Path) -> None:
    """Guard against a mutable-default bug in the builders above."""
    assert copy.deepcopy(minimal_ground()) == minimal_ground()
    assert load_spec(spec_dir).walls[0].length == 4000
