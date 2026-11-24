"""Meta-test: routes must use require(), not inline role inspection.

If this test fails, someone added a route that checks actor.roles directly
instead of going through require(). That bypasses the audit log and the
centralized policy. Fix the route, do not weaken this test.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2] / "app" / "api"
FORBIDDEN = [
    re.compile(r"actor\.roles\s*=="),
    re.compile(r"actor\.roles\s*!="),
    re.compile(r"actor\.role\b(?!s)"),
    re.compile(r"\.is_admin\b"),
    re.compile(r'if\s+["\']org_owner["\']\s+in'),
]


def test_routes_do_not_inspect_roles_inline():
    violations = []
    for path in ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pat in FORBIDDEN:
            for m in pat.finditer(text):
                line = text[:m.start()].count("\n") + 1
                violations.append(f"{path.relative_to(ROOT.parent.parent)}:{line}: {m.group(0)}")
    assert not violations, "Inline role checks found. Use require() instead:\n" + "\n".join(violations)