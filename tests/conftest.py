"""Shared pytest fixtures for the kotewki validation suite.

Most of the pipeline (spec.py, geometry.py, generator.py, export.py) is not
implemented yet — those are separate downstream tasks (T02, T07, T11, T12).
Until they land, the fixtures below skip gracefully with a clear reason
instead of erroring, so `just test` / `pytest` stays green (or cleanly
skipped) at every stage of the build-out.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC_DIR = REPO_ROOT / "spec"
DATA_DIR = REPO_ROOT / "data"
BUILD_DIR = REPO_ROOT / "build"
PUBLISHED_JSON = DATA_DIR / "published.json"
GLB_PATH = BUILD_DIR / "model.glb"


@pytest.fixture(scope="session")
def spec() -> dict[str, Any]:
    """The merged spec (spec/*.json assembled by kotewki.spec).

    Skips until T02's loader (src/kotewki/spec.py) and the transcribed
    spec/*.json files (T02/T04/T05) exist.
    """
    try:
        from kotewki import spec as spec_module
    except ImportError:
        pytest.skip("kotewki.spec is not implemented yet (T02)")

    load_fn = getattr(spec_module, "load", None) or getattr(spec_module, "load_spec", None)
    if load_fn is None:
        pytest.skip("kotewki.spec has no load()/load_spec() function yet (T02)")

    if not SPEC_DIR.exists() or not any(SPEC_DIR.glob("*.json")):
        pytest.skip("spec/*.json does not exist yet (T02/T04/T05)")

    try:
        return load_fn(SPEC_DIR)
    except Exception as exc:  # noqa: BLE001 - fixture is best-effort until spec lands
        pytest.skip(f"spec could not be loaded yet: {exc}")


@pytest.fixture(scope="session")
def model(spec: dict[str, Any]):
    """The built geometry model (walls, rooms, roof) from kotewki.geometry.

    Skips until T07 implements src/kotewki/geometry.py.
    """
    try:
        from kotewki import geometry
    except ImportError:
        pytest.skip("kotewki.geometry is not implemented yet (T07)")

    build_fn = getattr(geometry, "build", None) or getattr(geometry, "build_model", None)
    if build_fn is None:
        pytest.skip("kotewki.geometry has no build()/build_model() function yet (T07)")

    try:
        return build_fn(spec)
    except NotImplementedError:
        pytest.skip("kotewki.geometry.build() is not implemented yet (T07)")


@pytest.fixture(scope="session")
def published() -> dict[str, Any]:
    """The published reference figures (data/published.json), owned by T03."""
    if not PUBLISHED_JSON.exists():
        pytest.skip("data/published.json does not exist yet (T03)")
    with PUBLISHED_JSON.open(encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="session")
def glb_path() -> Path:
    """Path to the exported glTF artifact (build/model.glb), owned by T12.

    Skips (rather than failing) if the artifact has not been built yet -
    `just build` produces it.
    """
    if not GLB_PATH.exists():
        pytest.skip("build/model.glb does not exist yet - run `just build` (T12)")
    return GLB_PATH
