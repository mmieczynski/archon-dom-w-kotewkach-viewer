"""T03 — reconciliation of the published figures.

These tests do NOT touch geometry. They only assert that the numbers copied out of
the Archon project card into ``data/published.json`` are internally self-consistent.
If one of these fails, the transcription is wrong and must be fixed before any
downstream task (T08, T09) consumes the file.

All published figures are quoted to 2 decimal places, so comparisons use an
absolute tolerance of 0.005 m² — tight enough to catch a digit typo, loose enough
to absorb the publisher's own rounding.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLISHED_JSON = REPO_ROOT / "data" / "published.json"

# Published figures carry 2 decimals; half a hundredth of a m2 is the rounding floor.
TOL_M2 = 0.005

GROUND_LEVEL_TOTAL_M2 = 116.18  # "PARTER 116,18" printed above the room table
STAIRS_M2 = 3.64  # "4. Schody" — excluded from usable area, included in net area


@pytest.fixture(scope="session")
def published() -> dict:
    with PUBLISHED_JSON.open(encoding="utf-8") as fh:
        return json.load(fh)


def test_ground_room_areas_sum_to_level_total(published: dict) -> None:
    """sum(ground rooms) == 116.18"""
    rooms = published["rooms"]["ground"]
    assert len(rooms) == 14, f"expected 14 ground-floor rooms, got {len(rooms)}"
    total = sum(room["area_m2"] for room in rooms)
    assert total == pytest.approx(GROUND_LEVEL_TOTAL_M2, abs=TOL_M2), (
        f"ground room areas sum to {total:.2f} m2, published level total is "
        f"{GROUND_LEVEL_TOTAL_M2} m2"
    )


def test_usable_area_reconciles(published: dict) -> None:
    """116.18 + 14.51 + 14.67 + 18.21 == 163.57

    Usable area = ground floor + the three habitable attic rooms.
    The attic stair (Schody, 3.64) is deliberately NOT part of this sum.
    """
    attic_rooms = published["rooms"]["attic"]
    attic_excl_stairs = sum(
        room["area_m2"] for room in attic_rooms if room["name"] != "Schody"
    )
    total = GROUND_LEVEL_TOTAL_M2 + attic_excl_stairs
    expected = published["global"]["usable_area_m2"]
    assert total == pytest.approx(expected, abs=TOL_M2), (
        f"ground {GROUND_LEVEL_TOTAL_M2} + attic-excl-stairs {attic_excl_stairs:.2f} "
        f"= {total:.2f} m2, published usable area is {expected} m2"
    )


def test_net_area_reconciles(published: dict) -> None:
    """163.57 - 32.88 - 7.31 + 3.64 == 127.02

    Net area excludes the two 'Strych ocieplony' lofts (32.88) and the boiler room
    (7.31), but unlike usable area it DOES include the stair (3.64).
    """
    g = published["global"]
    total = (
        g["usable_area_m2"] - g["attic_area_m2"] - g["boiler_room_m2"] + STAIRS_M2
    )
    expected = g["net_area_m2"]
    assert total == pytest.approx(expected, abs=TOL_M2), (
        f"{g['usable_area_m2']} - {g['attic_area_m2']} - {g['boiler_room_m2']} "
        f"+ {STAIRS_M2} = {total:.2f} m2, published net area is {expected} m2"
    )


# --- supporting consistency checks (cheap, and they localise typos further) ---


def test_stairs_area_matches_constant(published: dict) -> None:
    stairs = [r for r in published["rooms"]["attic"] if r["name"] == "Schody"]
    assert len(stairs) == 1, "expected exactly one 'Schody' entry on the attic level"
    assert stairs[0]["area_m2"] == pytest.approx(STAIRS_M2, abs=TOL_M2)


def test_attic_area_is_the_two_lofts(published: dict) -> None:
    """'Powierzchnia strychu' 32.88 == the two 'Strych ocieplony' rooms."""
    lofts = [
        r["area_m2"]
        for r in published["rooms"]["attic"]
        if r["name"] == "Strych ocieplony"
    ]
    assert len(lofts) == 2, f"expected 2 'Strych ocieplony' rooms, got {len(lofts)}"
    assert sum(lofts) == pytest.approx(
        published["global"]["attic_area_m2"], abs=TOL_M2
    )


def test_boiler_room_matches_room_table(published: dict) -> None:
    boiler = [r for r in published["rooms"]["ground"] if r["name"] == "Kotłownia"]
    assert len(boiler) == 1
    assert boiler[0]["area_m2"] == pytest.approx(
        published["global"]["boiler_room_m2"], abs=TOL_M2
    )


def test_attic_level_total_matches_room_table(published: dict) -> None:
    """'PODDASZE 51,03' == all four attic entries including the stair."""
    total = sum(r["area_m2"] for r in published["rooms"]["attic"])
    assert total == pytest.approx(
        published["levels"]["attic"]["usable_area_m2"], abs=TOL_M2
    )


def test_section_levels_give_published_building_height(published: dict) -> None:
    """Ridge minus terrain, read off the section drawing, == published 7.09 m.

    Independent cross-check: the section is drawn evidence, the height is a
    tabulated figure, and they were transcribed separately.
    """
    s = published["section_levels_m"]
    height = s["ridge"] - s["terrain"]
    assert height == pytest.approx(
        published["global"]["building_height_m"], abs=0.005
    ), f"section gives {height:.2f} m ridge-above-terrain"


def test_measurement_norm_is_left_for_t15(published: dict) -> None:
    """T03 must not decide the norm; T15 owns that field."""
    assert published["measurement_norm"] is None
