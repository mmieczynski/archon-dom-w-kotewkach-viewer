# Build the 3D model artifact (build/model.glb) from spec/.
build:
    uv run python -m kotewki.generator

# Run the full pytest validation suite.
test:
    uv run pytest

# Run only the fast, geometry-free validators (chain closure + published-data
# reconciliation) - useful while transcribing a spec before geometry exists.
# Uses -k so it doesn't error before tests/test_chains.py exists (T06).
validate:
    uv run pytest -v -k "chain or published"

# Serve the three.js viewer against the built model.
viewer:
    cd viewer && python3 -m http.server 8000

# Lint the codebase.
lint:
    uv run ruff check .

# Install/sync the project environment.
sync:
    uv sync
