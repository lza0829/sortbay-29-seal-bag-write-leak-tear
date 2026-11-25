from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Keep pytest imports from touching a developer workspace database.
_TEST_DB = Path(tempfile.gettempdir()) / "sortbay-pytest-import.db"
os.environ.setdefault("SORTBAY_DB", str(_TEST_DB))
