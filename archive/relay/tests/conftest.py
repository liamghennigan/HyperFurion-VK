import sys
from pathlib import Path

import pytest

# The relay is an optional component with its own dependency (aiohttp);
# skip its whole suite cleanly where that dependency isn't installed so
# `pytest -q` at the repo root stays green for daemon-only environments.
pytest.importorskip("aiohttp")

_RELAY_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _RELAY_DIR.parent
for path in (str(_RELAY_DIR), str(_REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)
