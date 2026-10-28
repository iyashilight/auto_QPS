"""Read-only strategy health and downside-only automatic actions."""

import json

from qps.trading.advanced import LiveWatchdog


class StrategyHealthController:
    def __init__(self, audit_path: str | None = None) -> None:
        self.audit_path = audit_path

    def apply(self, status: str, *, deployment_id: str, reason: str) -> dict[str, object]:
        normalized = status.upper()
        if normalized in {"UNKNOWN", "DEGRADED", "CRITICAL"}:
            result: dict[str, object] = {
                "deployment_id": deployment_id,
                "action": "FREEZE" if normalized == "CRITICAL" else "RISK_REDUCE_ONLY",
                "reason": reason,
            }
        else:
            result = {"deployment_id": deployment_id, "action": "NONE", "reason": reason}
        if self.audit_path is not None:
            with open(self.audit_path, "a", encoding="utf-8") as stream:
                stream.write(json.dumps(result, sort_keys=True) + "\n")
        return result


__all__ = ["LiveWatchdog", "StrategyHealthController"]
