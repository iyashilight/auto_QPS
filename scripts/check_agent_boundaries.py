#!/usr/bin/env python3
"""Fail if Agent source imports trading/broker/storage mutation boundaries."""

from pathlib import Path

FORBIDDEN = ("qps.trading", "qps.broker", "qps.infra.storage", "qps.research.release")


def main() -> int:
    root = Path(__file__).resolve().parents[1] / "src" / "qps" / "agents"
    violations = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN:
            if token in text:
                violations.append(f"{path}: {token}")
    if violations:
        print("\n".join(violations))
        return 1
    print("agent boundaries passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
