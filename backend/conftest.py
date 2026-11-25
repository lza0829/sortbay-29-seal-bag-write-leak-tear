from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Avoid accidental writes to the developer workspace database when pytest is
# launched from repository root or through an IDE without PYTHONPATH=backend.
os.environ.setdefault(
    "SORTBAY_DB",
    str(Path(tempfile.gettempdir()) / "sortbay-pytest-root.db"),
)
