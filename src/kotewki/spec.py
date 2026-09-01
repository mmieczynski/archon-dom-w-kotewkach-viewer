"""Load, merge and validate the building spec.

The spec is split across three hand-transcribed files so that T02, T04 and T05 can be
written concurrently without write contention::

    spec/meta.json      levels, construction, section level marks, roof     (T02)
    spec/ground.json    walls/openings/rooms/chains for level "ground"      (T04)
    spec/attic.json     walls/openings/rooms/chains for level "attic"       (T05)

`load_spec()` concatenates them into one document, validates it against
``spec/schema.json``, runs the cross-file checks JSON Schema cannot express, and returns a
:class:`Spec`. There is deliberately no way to obtain an unvalidated spec: every failure
raises :class:`SpecValidationError`, and every error found is reported at once rather than
one per run.

UNITS
-----
Everything in the spec is integer millimetres except:

* ``dimension_chains[].segments_cm`` / ``total_cm`` — centimetres, verbatim as printed on
  the plans. :data:`CM_TO_MM` below is the **only** place in the codebase that converts
  them, via :attr:`DimensionChain.segments_mm` / :attr:`DimensionChain.total_mm`.
* ``published_area`` / ``floor_area_m2`` — float m², as printed.
* ``roof.pitch_deg`` — float degrees.

The loader sweeps every parsed value and rejects a float anywhere else, which is stricter
than the schema can be: JSON Schema's ``"type": "integer"`` accepts ``250.0``.
"""

from __future__ import annotations

import json
import math
import warnings
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

__all__ = [
    "CM_TO_MM",
    "DimensionChain",
    "Layer",
    "Level",
    "Opening",
    "Roof",
    "Room",
    "RoomMeasurement",
    "Spec",
    "SpecError",
    "SpecValidationError",
    "Wall",
    "load",
    "load_spec",
]

# --------------------------------------------------------------------------------------
# The one and only unit conversion in the codebase.
# --------------------------------------------------------------------------------------

#: Centimetres (as printed on the plans, stored in the ``*_cm`` fields) to millimetres.
#: Do not inline this factor anywhere else. A missed x10 yields a house one tenth the size
#: that still closes every dimension chain -- see the "Units" section of README.md.
CM_TO_MM = 10

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_DIR = REPO_ROOT / "spec"
SCHEMA_PATH = SPEC_DIR / "schema.json"

META_FILE = "meta.json"
GROUND_FILE = "ground.json"
ATTIC_FILE = "attic.json"

#: Collections that live in the per-level files and are concatenated on merge.
COLLECTIONS: tuple[str, ...] = ("walls", "openings", "rooms", "dimension_chains", "slab_openings")

#: Keys each source file is allowed to contain. Enforced so that, for example, T04 cannot
#: accidentally redefine `levels` inside ground.json and have it silently win the merge.
ALLOWED_KEYS: dict[str, frozenset[str]] = {
    META_FILE: frozenset(
        {"meta", "levels", "construction", "section_elevations", "roof", "roof_openings"}
    ),
    GROUND_FILE: frozenset(COLLECTIONS),
    ATTIC_FILE: frozenset(COLLECTIONS),
}

#: The level every entity in a given per-level file must declare.
FILE_LEVEL: dict[str, str] = {GROUND_FILE: "ground", ATTIC_FILE: "attic"}

#: The id prefix every entity in a given per-level file must carry.
FILE_ID_PREFIX: dict[str, str] = {GROUND_FILE: "G_", ATTIC_FILE: "A_"}

#: The only keys whose values may be JSON floats. Everything else is integer millimetres.
FLOAT_FIELDS: frozenset[str] = frozenset({"published_area", "floor_area_m2", "pitch_deg"})

#: Annotation keys shared by every object in the schema.
_ANNOTATION_KEYS = (
    "note",
    "source_image",
    "derived",
    "derived_fields",
    "disputed",
    "disputed_fields",
    "dispute_ref",
)


class SpecError(Exception):
    """Base class for every spec-loading failure."""


class SpecValidationError(SpecError):
    """The merged spec is invalid. Carries every problem found, not just the first."""

    def __init__(self, errors: list[str], *, context: str = "spec validation failed") -> None:
        self.errors = list(errors)
        body = "\n".join(f"  {i + 1}. {e}" for i, e in enumerate(self.errors))
        super().__init__(f"{context} ({len(self.errors)} problem(s)):\n{body}")


# --------------------------------------------------------------------------------------
# Typed views
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class Annotated:
    """Provenance annotations shared by every spec object.

    ``derived`` marks a value that was inferred rather than read off a printed number;
    ``disputed`` marks a value that was transcribed faithfully but contradicts another
    published figure. They are independent: ``roof.pitch_deg`` is transcribed (not derived)
    and disputed.
    """

    note: str | None = None
    source_image: str | None = None
    derived: bool = False
    derived_fields: tuple[str, ...] = ()
    disputed: bool = False
    disputed_fields: tuple[str, ...] = ()
    dispute_ref: str | None = None

    def is_derived(self, field_name: str) -> bool:
        """True if ``field_name`` on this object was inferred rather than transcribed."""
        return self.derived or field_name in self.derived_fields

    def is_disputed(self, field_name: str) -> bool:
        """True if ``field_name`` on this object is subject to an unresolved dispute."""
        return self.disputed or field_name in self.disputed_fields


def _annotations(raw: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in _ANNOTATION_KEYS:
        if key in raw:
            value = raw[key]
            out[key] = tuple(value) if key.endswith("_fields") else value
    return out


@dataclass(frozen=True, kw_only=True)
class Layer(Annotated):
    """One material layer of a wall or slab build-up. ``thickness`` is millimetres."""

    material: str
    thickness: int

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any]) -> Layer:
        return cls(material=raw["material"], thickness=raw["thickness"], **_annotations(raw))


@dataclass(frozen=True, kw_only=True)
class Level(Annotated):
    """A storey. ``elevation`` and ``ceiling_height`` are millimetres."""

    id: str
    name: str
    elevation: int
    ceiling_height: int

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any]) -> Level:
        return cls(
            id=raw["id"],
            name=raw["name"],
            elevation=raw["elevation"],
            ceiling_height=raw["ceiling_height"],
            **_annotations(raw),
        )


@dataclass(frozen=True, kw_only=True)
class Wall(Annotated):
    """A wall centreline. All lengths millimetres, coordinates in the shared frame."""

    id: str
    level: str
    start: tuple[int, int]
    end: tuple[int, int]
    thickness: int
    type: str
    layers: tuple[Layer, ...] = ()
    height: int | None = None

    @property
    def length(self) -> float:
        """Centreline length in millimetres. Float only because of the hypotenuse."""
        return math.dist(self.start, self.end)

    @property
    def is_axis_aligned(self) -> bool:
        return self.start[0] == self.end[0] or self.start[1] == self.end[1]

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any]) -> Wall:
        return cls(
            id=raw["id"],
            level=raw["level"],
            start=(raw["start"][0], raw["start"][1]),
            end=(raw["end"][0], raw["end"][1]),
            thickness=raw["thickness"],
            type=raw["type"],
            layers=tuple(Layer.from_raw(item) for item in raw.get("layers", ())),
            height=raw.get("height"),
            **_annotations(raw),
        )


@dataclass(frozen=True, kw_only=True)
class Opening(Annotated):
    """A window, door or cased passage. All lengths millimetres."""

    id: str
    wall: str
    offset: int
    width: int
    height: int
    sill: int
    kind: str
    swing: str = "none"

    @property
    def head(self) -> int:
        """Elevation of the opening head above the level's finished floor, millimetres."""
        return self.sill + self.height

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any]) -> Opening:
        return cls(
            id=raw["id"],
            wall=raw["wall"],
            offset=raw["offset"],
            width=raw["width"],
            height=raw["height"],
            sill=raw["sill"],
            kind=raw["kind"],
            swing=raw.get("swing", "none"),
            **_annotations(raw),
        )


@dataclass(frozen=True)
class RoomMeasurement:
    """``rooms[].measure_to`` -- one room's override of the measurement convention.

    Exists so that the project's single measurement carve-out is **data in the spec**
    rather than a room id branched on inside the geometry kernel. See
    ``spec/schema.json`` ``$defs.room_measurement`` for the full description, and A_R4's
    ``note`` in ``spec/attic.json`` for the justification of the only instance of it.
    """

    #: ``"structure"`` (no finish allowance on any edge) or ``"finish"`` (w swietle scian).
    face: str
    #: Boundary walls whose footprint lies inside this room's plan projection.
    extends_under: tuple[str, ...] = ()

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any]) -> RoomMeasurement:
        return cls(
            face=raw["face"],
            extends_under=tuple(raw.get("extends_under", ())),
        )


@dataclass(frozen=True, kw_only=True)
class Room(Annotated):
    """A room. Areas are float m2 exactly as the publisher prints them."""

    id: str
    published_id: int
    name: str
    level: str
    boundary: tuple[str, ...]
    published_area: float
    floor_area_m2: float | None = None
    seed: tuple[int, int] | None = None
    measure_to: RoomMeasurement | None = None
    area_groups: frozenset[str] = frozenset()

    @property
    def in_usable_area(self) -> bool:
        """Counted in powierzchnia uzytkowa (163.57 m2). False for the stairs."""
        return "usable" in self.area_groups

    @property
    def in_net_area(self) -> bool:
        """Counted in powierzchnia netto (127.02 m2). True for the stairs."""
        return "net" in self.area_groups

    @property
    def is_attic_area(self) -> bool:
        """One of the two 'Strych ocieplony' rooms summing to the published 32.88 m2."""
        return "attic" in self.area_groups

    @property
    def is_boiler_room(self) -> bool:
        return "boiler" in self.area_groups

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any]) -> Room:
        seed = raw.get("seed")
        measure_to = raw.get("measure_to")
        return cls(
            id=raw["id"],
            published_id=raw["published_id"],
            name=raw["name"],
            level=raw["level"],
            boundary=tuple(raw["boundary"]),
            published_area=raw["published_area"],
            floor_area_m2=raw.get("floor_area_m2"),
            seed=(seed[0], seed[1]) if seed else None,
            measure_to=RoomMeasurement.from_raw(measure_to) if measure_to else None,
            area_groups=frozenset(raw["area_groups"]),
            **_annotations(raw),
        )


@dataclass(frozen=True, kw_only=True)
class DimensionChain(Annotated):
    """A printed dimension run.

    ``segments_cm`` and ``total_cm`` are centimetres, verbatim as printed. Use
    :attr:`segments_mm` / :attr:`total_mm` for millimetres -- they are the only x10 in the
    codebase. T06 asserts closure on the **cm** values, before conversion.
    """

    id: str
    level: str
    axis: str
    segments_cm: tuple[int, ...]
    total_cm: int
    source_image: str
    extent: str | None = None

    @property
    def segments_mm(self) -> tuple[int, ...]:
        return tuple(value * CM_TO_MM for value in self.segments_cm)

    @property
    def total_mm(self) -> int:
        return self.total_cm * CM_TO_MM

    @property
    def sum_cm(self) -> int:
        return sum(self.segments_cm)

    @property
    def closes(self) -> bool:
        """Exact integer equality. There is no tolerance and none should be added."""
        return self.sum_cm == self.total_cm

    @property
    def delta_cm(self) -> int:
        """``sum(segments) - printed total``. A delta of ~100 is usually a misread digit;
        a delta the size of a whole segment is usually a dropped segment."""
        return self.sum_cm - self.total_cm

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any]) -> DimensionChain:
        annotations = _annotations(raw)
        annotations.pop("source_image", None)
        return cls(
            id=raw["id"],
            level=raw["level"],
            axis=raw["axis"],
            segments_cm=tuple(raw["segments_cm"]),
            total_cm=raw["total_cm"],
            source_image=raw["source_image"],
            extent=raw.get("extent"),
            **annotations,
        )


@dataclass(frozen=True, kw_only=True)
class Roof(Annotated):
    """Roof INPUTS. The ridge height is not here: it is an output of the generator.

    ``pitch_deg`` is currently disputed -- see ``spec/meta.json`` and tasks/T17.md.
    """

    type: str
    pitch_deg: float
    eaves_overhang: int
    ridge_axis: str | None = None
    springing: str | None = None

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any]) -> Roof:
        return cls(
            type=raw["type"],
            pitch_deg=raw["pitch_deg"],
            eaves_overhang=raw["eaves_overhang"],
            ridge_axis=raw.get("ridge_axis"),
            springing=raw.get("springing"),
            **_annotations(raw),
        )


# --------------------------------------------------------------------------------------
# The Spec container
# --------------------------------------------------------------------------------------


class Spec(Mapping):
    """A validated, merged spec.

    Behaves as a read-only mapping over the merged JSON document, so raw access works as
    written in TESTS.md::

        for chain in spec["dimension_chains"]:
            assert sum(chain["segments_cm"]) == chain["total_cm"]

    and typed access works too::

        for chain in spec.dimension_chains:
            assert chain.closes, chain.id
    """

    def __init__(self, raw: dict[str, Any], *, sources: dict[str, Path] | None = None) -> None:
        self._raw = raw
        self.sources = dict(sources or {})

    # -- Mapping protocol ---------------------------------------------------------------

    def __getitem__(self, key: str) -> Any:
        return self._raw[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._raw)

    def __len__(self) -> int:
        return len(self._raw)

    def __repr__(self) -> str:
        return (
            f"<Spec levels={len(self._raw.get('levels', []))} "
            f"walls={len(self._raw.get('walls', []))} "
            f"rooms={len(self._raw.get('rooms', []))} "
            f"chains={len(self._raw.get('dimension_chains', []))}>"
        )

    def to_dict(self) -> dict[str, Any]:
        """The merged document as a plain dict (a shallow copy)."""
        return dict(self._raw)

    # -- Typed views ---------------------------------------------------------------------

    @cached_property
    def meta(self) -> dict[str, Any]:
        return self._raw["meta"]

    @cached_property
    def construction(self) -> dict[str, Any]:
        return self._raw["construction"]

    @cached_property
    def section_elevations(self) -> dict[str, Any]:
        return self._raw["section_elevations"]

    @cached_property
    def levels(self) -> tuple[Level, ...]:
        return tuple(Level.from_raw(item) for item in self._raw["levels"])

    @cached_property
    def walls(self) -> tuple[Wall, ...]:
        return tuple(Wall.from_raw(item) for item in self._raw["walls"])

    @cached_property
    def openings(self) -> tuple[Opening, ...]:
        return tuple(Opening.from_raw(item) for item in self._raw["openings"])

    @cached_property
    def rooms(self) -> tuple[Room, ...]:
        return tuple(Room.from_raw(item) for item in self._raw["rooms"])

    @cached_property
    def dimension_chains(self) -> tuple[DimensionChain, ...]:
        return tuple(DimensionChain.from_raw(item) for item in self._raw["dimension_chains"])

    @cached_property
    def roof(self) -> Roof:
        return Roof.from_raw(self._raw["roof"])

    # -- Lookups -------------------------------------------------------------------------

    @cached_property
    def level_by_id(self) -> dict[str, Level]:
        return {item.id: item for item in self.levels}

    @cached_property
    def wall_by_id(self) -> dict[str, Wall]:
        return {item.id: item for item in self.walls}

    @cached_property
    def opening_by_id(self) -> dict[str, Opening]:
        return {item.id: item for item in self.openings}

    @cached_property
    def room_by_id(self) -> dict[str, Room]:
        return {item.id: item for item in self.rooms}

    @cached_property
    def chain_by_id(self) -> dict[str, DimensionChain]:
        return {item.id: item for item in self.dimension_chains}

    def walls_on(self, level: str) -> tuple[Wall, ...]:
        return tuple(item for item in self.walls if item.level == level)

    def rooms_on(self, level: str) -> tuple[Room, ...]:
        return tuple(item for item in self.rooms if item.level == level)

    def chains_on(self, level: str) -> tuple[DimensionChain, ...]:
        return tuple(item for item in self.dimension_chains if item.level == level)

    def openings_in(self, wall_id: str) -> tuple[Opening, ...]:
        return tuple(item for item in self.openings if item.wall == wall_id)


# --------------------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------------------


def load_schema(schema_path: str | Path | None = None) -> dict[str, Any]:
    """Read ``spec/schema.json``."""
    path = Path(schema_path) if schema_path is not None else SCHEMA_PATH
    if not path.is_file():
        raise SpecError(f"schema not found at {path}")
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as handle:
            document = json.load(handle)
    except json.JSONDecodeError as exc:
        raise SpecError(f"{path.name} is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise SpecError(f"{path.name} must contain a JSON object at the top level")
    return document


def _sweep_floats(node: Any, path: str, key: str | None, errors: list[str]) -> None:
    """Reject any JSON float outside :data:`FLOAT_FIELDS`.

    Stricter than the schema on purpose: JSON Schema's ``"type": "integer"`` accepts
    ``250.0`` because it has no fractional part, so ``"thickness": 250.0`` would slip
    through. Every length in this spec is an integer number of millimetres; a float there
    means someone typed metres, or did arithmetic they were not supposed to do.
    """
    if isinstance(node, dict):
        for child_key, value in node.items():
            _sweep_floats(value, f"{path}.{child_key}", child_key, errors)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _sweep_floats(value, f"{path}[{index}]", key, errors)
    elif isinstance(node, bool):
        return
    elif isinstance(node, float) and key not in FLOAT_FIELDS:
        errors.append(
            f"{path}: float value {node!r}. Every length in the spec is an INTEGER number "
            f"of millimetres (and dimension chains are integer centimetres in *_cm "
            f"fields). Floats are permitted only for {sorted(FLOAT_FIELDS)}. If this is "
            f"metres, multiply by 1000; if it is centimetres, it belongs in a *_cm field."
        )


def _check_file_keys(filename: str, document: Mapping[str, Any], errors: list[str]) -> None:
    allowed = ALLOWED_KEYS[filename]
    for key in document:
        if key == "_comment":
            continue
        if key not in allowed:
            owner = next(
                (name for name, keys in ALLOWED_KEYS.items() if key in keys),
                "no file",
            )
            errors.append(
                f"{filename}: key {key!r} does not belong in this file "
                f"(it belongs in {owner}). Allowed here: {sorted(allowed)}. "
                f"Cross-file writes are what the three-file split exists to prevent."
            )


def _check_file_ownership(filename: str, document: Mapping[str, Any], errors: list[str]) -> None:
    """Every entity in a per-level file must declare that level and carry its id prefix."""
    expected_level = FILE_LEVEL[filename]
    prefix = FILE_ID_PREFIX[filename]
    for collection in COLLECTIONS:
        for index, entity in enumerate(document.get(collection, ())):
            if not isinstance(entity, dict):
                continue
            where = f"{filename}:{collection}[{index}]"
            entity_id = entity.get("id")
            if isinstance(entity_id, str) and not entity_id.startswith(prefix):
                errors.append(
                    f"{where}: id {entity_id!r} must start with {prefix!r} "
                    f"(ids are level-prefixed so the three-file merge cannot collide)."
                )
            level = entity.get("level")
            if level is not None and level != expected_level:
                errors.append(
                    f"{where} (id {entity_id!r}): level is {level!r} but everything in "
                    f"{filename} must be on level {expected_level!r}."
                )


def _check_id_uniqueness(filename: str, document: Mapping[str, Any], seen: dict[str, str],
                         errors: list[str]) -> None:
    """Assert global id uniqueness as each file is merged in.

    Runs during the merge rather than after schema validation, so that a collision is
    reported by name even when the offending file has other problems. Without this the
    merge would be last-write-wins and one of the two entities would silently vanish.
    """
    for collection in COLLECTIONS:
        for entity in document.get(collection, ()):
            if not isinstance(entity, dict):
                continue
            entity_id = entity.get("id")
            if not isinstance(entity_id, str):
                continue
            origin = f"{filename}:{collection}"
            if entity_id in seen:
                errors.append(
                    f"duplicate id {entity_id!r}: defined in {seen[entity_id]} and again in "
                    f"{origin}. Ids must be globally unique across the merged spec -- the "
                    f"loader asserts uniqueness rather than letting the last file written "
                    f"win, which would silently drop one of the two entities."
                )
            else:
                seen[entity_id] = origin


def _validate_against_schema(document: Mapping[str, Any], schema: Mapping[str, Any]) -> list[str]:
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:  # pragma: no cover - environment problem, not a spec problem
        raise SpecError(
            "jsonschema is required to load the spec (downstream code must never see an "
            "unvalidated spec). Install it: `uv sync`."
        ) from exc

    validator = Draft202012Validator(schema)
    errors = []
    for error in sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path)):
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        errors.append(f"schema: {location}: {error.message}")
    return errors


def _check_semantics(document: Mapping[str, Any]) -> list[str]:
    """Cross-references and invariants JSON Schema cannot express.

    Runs only after the schema has passed, so it may assume well-formed structure.
    """
    errors: list[str] = []

    level_ids = {item["id"] for item in document["levels"]}
    wall_ids = {item["id"] for item in document["walls"]}
    wall_levels = {item["id"]: item["level"] for item in document["walls"]}

    def _check_level(where: str, level: str) -> None:
        if level not in level_ids:
            errors.append(
                f"{where}: level {level!r} is not defined in levels[] "
                f"(defined: {sorted(level_ids)})."
            )

    for wall in document["walls"]:
        _check_level(f"wall {wall['id']}", wall["level"])
        if wall["start"] == wall["end"]:
            errors.append(f"wall {wall['id']}: zero-length (start == end {wall['start']}).")
        layers = wall.get("layers")
        if layers:
            total = sum(layer["thickness"] for layer in layers)
            if total != wall["thickness"]:
                errors.append(
                    f"wall {wall['id']}: layers sum to {total} mm but thickness is "
                    f"{wall['thickness']} mm."
                )

    for opening in document["openings"]:
        if opening["wall"] not in wall_ids:
            errors.append(
                f"opening {opening['id']}: references wall {opening['wall']!r}, which does "
                f"not exist. An opening with no host wall would be silently dropped by the "
                f"generator."
            )

    for room in document["rooms"]:
        _check_level(f"room {room['id']}", room["level"])
        for wall_id in room["boundary"]:
            if wall_id not in wall_ids:
                errors.append(
                    f"room {room['id']}: boundary references wall {wall_id!r}, "
                    f"which does not exist."
                )
            elif wall_levels[wall_id] != room["level"]:
                errors.append(
                    f"room {room['id']} (level {room['level']!r}): boundary wall {wall_id!r} "
                    f"is on level {wall_levels[wall_id]!r}."
                )
        # measure_to.extends_under grows the room polygon across a wall, so the wall has
        # to be one this room is actually bounded by. Naming a wall elsewhere on the level
        # would silently annex a strip of a different room.
        for wall_id in (room.get("measure_to") or {}).get("extends_under", ()):
            if wall_id not in room["boundary"]:
                errors.append(
                    f"room {room['id']}: measure_to.extends_under names wall {wall_id!r}, "
                    f"which is not in this room's boundary {list(room['boundary'])}. A "
                    f"room may only extend under a wall that bounds it."
                )
        groups = set(room["area_groups"])
        for exclusive in ("attic", "boiler"):
            if exclusive in groups and "net" in groups:
                errors.append(
                    f"room {room['id']} ({room['name']!r}): area_groups has both "
                    f"{exclusive!r} and 'net', but powierzchnia netto (127.02 m2) excludes "
                    f"the attic rooms and the boiler room. See README.md's reconciliation."
                )

    for chain in document["dimension_chains"]:
        _check_level(f"chain {chain['id']}", chain["level"])

    # -- provenance annotations must name real fields -----------------------------------
    errors.extend(_check_provenance(document, "<root>"))

    return errors


def _check_provenance(node: Any, path: str) -> list[str]:
    """``derived_fields`` / ``disputed_fields`` must name properties that actually exist.

    Without this a typo (``derived_fields: ["eave_overhang"]`` for ``eaves_overhang``)
    silently annotates nothing, and a derived value gets presented as transcribed fact.
    """
    errors: list[str] = []
    if isinstance(node, dict):
        for annotation in ("derived_fields", "disputed_fields"):
            for name in node.get(annotation, ()):
                if name not in node:
                    errors.append(
                        f"{path}: {annotation} names {name!r}, which is not a field of this "
                        f"object (has: {sorted(k for k in node if not k.startswith('_'))})."
                    )
        for key, value in node.items():
            errors.extend(_check_provenance(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            errors.extend(_check_provenance(value, f"{path}[{index}]"))
    return errors


def load_spec(
    spec_dir: str | Path | None = None,
    *,
    require_all_files: bool = False,
    schema_path: str | Path | None = None,
) -> Spec:
    """Merge ``meta.json`` + ``ground.json`` + ``attic.json`` and validate the result.

    Args:
        spec_dir: directory holding the three spec files. Defaults to ``<repo>/spec``.
        require_all_files: when False (the default) a missing ``ground.json`` or
            ``attic.json`` produces a :class:`UserWarning` and an empty level instead of an
            error, so T06-T14 can build against fixtures before transcription lands.
            ``meta.json`` is always required -- without it there are no levels, no
            construction and no roof, and nothing downstream can run.
        schema_path: override for ``spec/schema.json``, for tests.

    Raises:
        SpecError: a file is missing, unreadable, or not JSON.
        SpecValidationError: the merged document violates the schema or a cross-file rule.
    """
    directory = Path(spec_dir) if spec_dir is not None else SPEC_DIR
    if not directory.is_dir():
        raise SpecError(f"spec directory not found: {directory}")

    merged: dict[str, Any] = {collection: [] for collection in COLLECTIONS}
    sources: dict[str, Path] = {}
    errors: list[str] = []
    seen_ids: dict[str, str] = {}

    meta_path = directory / META_FILE
    if not meta_path.is_file():
        raise SpecError(
            f"{meta_path} is required (it carries levels, construction, "
            f"section_elevations and roof) and was not found."
        )

    for filename in (META_FILE, GROUND_FILE, ATTIC_FILE):
        path = directory / filename
        if not path.is_file():
            message = (
                f"{filename} not found in {directory}; continuing with an empty "
                f"{FILE_LEVEL[filename]} level. Transcription for it has not landed yet "
                f"(T04 owns ground.json, T05 owns attic.json)."
            )
            if require_all_files:
                raise SpecError(message)
            warnings.warn(message, UserWarning, stacklevel=2)
            continue

        document = _read_json(path)
        sources[filename] = path
        _check_file_keys(filename, document, errors)
        _sweep_floats(document, filename, None, errors)
        _check_id_uniqueness(filename, document, seen_ids, errors)
        if filename in FILE_LEVEL:
            _check_file_ownership(filename, document, errors)

        for key, value in document.items():
            if key == "_comment":
                continue
            if key in COLLECTIONS:
                merged[key].extend(value)
            else:
                merged[key] = value

    schema = load_schema(schema_path)
    errors.extend(_validate_against_schema(merged, schema))

    if errors:
        raise SpecValidationError(errors, context=f"spec in {directory} is invalid")

    errors = _check_semantics(merged)
    if errors:
        raise SpecValidationError(errors, context=f"spec in {directory} is invalid")

    return Spec(merged, sources=sources)


#: Alias. ``tests/conftest.py`` looks for ``load`` first, then ``load_spec``.
load = load_spec
