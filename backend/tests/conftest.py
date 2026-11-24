"""Point the app at a separate *_test database. Never touches dev data."""
from __future__ import annotations

import os
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def _read_dotenv() -> dict[str, str]:
    out: dict[str, str] = {}
    p = BACKEND / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            m = re.match(r"^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$", line)
            if m:
                out[m.group(1)] = m.group(2).strip('"').strip("'")
    return out


def _test_url(url: str) -> str:
    return re.sub(r"/([^/?]+)(\?.*)?$", lambda m: "/auralink_test" + (m.group(2) or ""), url)


_env = {**_read_dotenv(), **os.environ}
for _key in ("DATABASE_URL", "ALEMBIC_DATABASE_URL"):
    if _key in _env:
        os.environ[_key] = _test_url(_env[_key])
        assert os.environ[_key].split("?")[0].endswith("/auralink_test"), "refusing to run on non-test DB"
