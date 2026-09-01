# Dom w Kotewkach 6 (E) -- build and validation pipeline.
#
#   just build          spec -> validate -> generate -> export -> overlay
#   just validate       the fast, geometry-free validators (the inner loop, ~2 s)
#   just test           the full suite, fail-fast, in cost order
#   just check          ruff + the full suite. One command; the exit code is the verdict.
#   just report         build/REPORT.md
#   just install-hooks  install the checked-in pre-commit hook
#
# THE REPOSITORY IS LOCAL ONLY (user decision, 2026-08-31): no remote, no GitHub Actions,
# no .github/. data/source/ holds Archon's copyrighted plan bitmaps, so publishing the
# repo would redistribute them. `just check` plus the pre-commit hook are the local
# equivalent of the CI that was originally specified. If a remote ever appears, the
# workflow is a thin wrapper over `just check` and nothing here needs to change.
#
# NEVER hand-edit build/. It is generated; fix the spec and rebuild.
#
# Test modules run in the order below: roughly cost-ascending AND diagnostic-value-
# descending (see TESTS.md). A chain-closure failure names the wrong number; an overlay
# failure only says something is off somewhere. Surfacing the former first saves real time.

# Fast validators: no 3D generation, no mesh, no raster. `just validate` and the
# pre-commit hook run exactly these.
FAST := "tests/test_schema.py tests/test_published.py tests/test_chains.py tests/test_geometry.py tests/test_topology.py tests/test_room_areas.py"

# The rest, in cost order: generator, then the checks needing a built scene or artifact.
SLOW := "tests/test_generator.py tests/test_invariants.py tests/test_export.py tests/test_overlay.py"

# List the recipes.
default:
    @just --list --unsorted

# Install/sync the project environment.
sync:
    uv sync

# Build build/model.glb, build/quantities.json and build/overlay_*.png.
build:
    #!/usr/bin/env bash
    # "spec -> validate" is not a separate step because it cannot be skipped:
    # kotewki.spec.load_spec() validates the merged document against spec/schema.json and
    # refuses to return an invalid spec, so nothing downstream can be generated from one.
    #
    # The overlays are a side effect of tests/test_overlay.py, which owns the rasteriser.
    # Running it here is what makes them part of the build rather than of a test run.
    #
    # Nothing is deleted first and nothing is deleted on failure. build/overlay_*.png is
    # the primary debugging aid for a failed overlay, and wiping it removes the evidence
    # exactly when it is needed. Use `just clean` if you really want a bare build/.
    set -euo pipefail
    cd "{{ justfile_directory() }}"
    trap 'echo "" >&2; echo "just build FAILED -- build/ has been left exactly as it is, on purpose. Prior artifacts are not wiped and partial output is not deleted; build/overlay_*.png in particular is the evidence for a failed overlay." >&2' ERR
    mkdir -p build
    uv run python -m kotewki.export
    uv run pytest -q tests/test_overlay.py

# The fast validators only (~2 s): schema, published data, chains, kernel, topology, areas.
validate *ARGS:
    #!/usr/bin/env bash
    # No 3D generation, no export, no raster. This is the inner loop while transcribing a
    # spec, and it is what the pre-commit hook runs -- a hook slow enough to be annoying
    # gets bypassed with --no-verify, and a bypassed hook guards nothing.
    set -euo pipefail
    cd "{{ justfile_directory() }}"
    files=""
    for f in {{ FAST }}; do
      if [ -f "$f" ]; then files="$files $f"; fi
    done
    if [ -z "$files" ]; then echo "no validator modules found" >&2; exit 1; fi
    uv run pytest -x -q {{ ARGS }} $files

# The full suite, fail-fast, in cost order.
test *ARGS:
    #!/usr/bin/env bash
    set -euo pipefail
    cd "{{ justfile_directory() }}"
    uv run pytest -x -q {{ ARGS }} $({{ just_executable() }} _ordered | tr '\n' ' ')

# Print the suite in the order `just test` runs it.
_ordered:
    #!/usr/bin/env bash
    # Ordered modules first, then any tests/test_*.py not named in FAST or SLOW -- so
    # adding a test module can never silently exclude it from `just test`/`just check`.
    set -euo pipefail
    cd "{{ justfile_directory() }}"
    known=" {{ FAST }} {{ SLOW }} "
    for f in {{ FAST }} {{ SLOW }}; do
      if [ -f "$f" ]; then printf '%s\n' "$f"; fi
    done
    for f in tests/test_*.py; do
      case "$known" in *" $f "*) ;; *) printf '%s\n' "$f" ;; esac
    done

# Lint the codebase.
lint *ARGS:
    uv run ruff check {{ ARGS }} .

# THE VERDICT: ruff plus the full suite. One command, and the exit code is the answer.
check:
    #!/usr/bin/env bash
    # The local stand-in for CI. Any future CI workflow should call this rather than
    # re-listing the steps, so the two can never drift apart.
    set -euo pipefail
    cd "{{ justfile_directory() }}"
    started=$SECONDS
    echo "==> ruff"
    {{ just_executable() }} lint
    echo "==> pytest, fail-fast, cost order"
    {{ just_executable() }} test
    echo ""
    echo "==> OK in $((SECONDS - started))s. The model is well-evidenced, not proven -- see build/REPORT.md."

# Generate build/REPORT.md from the spec and build/quantities.json.
report *ARGS:
    #!/usr/bin/env bash
    set -euo pipefail
    cd "{{ justfile_directory() }}"
    if [ ! -f build/quantities.json ]; then
      echo "build/quantities.json is missing -- running just build first." >&2
      {{ just_executable() }} build
    fi
    uv run python -m kotewki.report {{ ARGS }}

# Install the checked-in pre-commit hook into .git/hooks/.
install-hooks:
    #!/usr/bin/env bash
    # .git/hooks/ is not version-controlled, so the hook lives in hooks/ and this symlinks
    # it into place; edits to hooks/pre-commit then take effect with no reinstall.
    set -euo pipefail
    cd "{{ justfile_directory() }}"
    git rev-parse --git-dir >/dev/null
    hooks_dir="$(git rev-parse --git-path hooks)"
    mkdir -p "$hooks_dir"
    target="$hooks_dir/pre-commit"
    if [ -e "$target" ] && [ ! -L "$target" ]; then
      backup="$target.replaced-by-just-install-hooks"
      mv "$target" "$backup"
      echo "an existing non-symlink hook was moved to $backup" >&2
    fi
    chmod +x hooks/pre-commit
    ln -sfn "$PWD/hooks/pre-commit" "$target"
    echo "installed $target -> hooks/pre-commit  (runs: just validate)"

# Remove the pre-commit hook symlink this repo installed.
uninstall-hooks:
    #!/usr/bin/env bash
    set -euo pipefail
    cd "{{ justfile_directory() }}"
    target="$(git rev-parse --git-path hooks)/pre-commit"
    if [ -L "$target" ]; then rm "$target"; echo "removed $target"; else echo "no symlinked hook at $target; nothing removed"; fi

# Serve the three.js viewer against the built model.
#
# The server root is the REPOSITORY ROOT, not viewer/. The viewer reads build/model.glb and
# build/quantities.json, which sit beside viewer/ rather than inside it, and http.server
# refuses to serve outside its root -- `cd viewer` yields a viewer with nothing to view.
viewer port="8000":
    #!/usr/bin/env bash
    set -euo pipefail
    cd "{{ justfile_directory() }}"
    if [ ! -f build/model.glb ]; then
      echo "build/model.glb is missing -- running just build first." >&2
      {{ just_executable() }} build
    fi
    echo "viewer: http://localhost:{{ port }}/viewer/"
    python3 -m http.server {{ port }}

# Delete generated artifacts. Explicit and opt-in; no other recipe wipes build/.
clean:
    #!/usr/bin/env bash
    # Artifacts are most valuable exactly when something has just failed, which is why
    # emptying build/ is something you ask for and never something that happens to you.
    set -euo pipefail
    cd "{{ justfile_directory() }}"
    find build -mindepth 1 ! -name .gitkeep -delete
    echo "build/ emptied (build/.gitkeep kept)"
