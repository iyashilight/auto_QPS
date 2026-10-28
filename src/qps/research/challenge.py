"""Blind Challenge 受限 runner。

普通研究对象只拿到 result hash；原始区间/答案从不经过 ExperimentStore 或 CLI
返回。生产接线应由独立 runner 进程加载本模块。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

from qps.core.errors import DataError
from qps.core.models.foundation import BlindChallengeResult
from qps.infra.storage.research_db import ResearchStore, canonical_json


class BlindDatasetVault:
    """只有 runner 内部持有的盲数据容器；没有面向普通 actor 的读取 API。"""

    def __init__(self, raw: Mapping[str, Any]) -> None:
        self._raw = dict(raw)

    def public_read(self, actor: str) -> None:
        raise PermissionError(f"actor {actor} 无权读取 blind 原始域")


class BlindChallengeRunner:
    def __init__(self, *, vault: BlindDatasetVault, budget_port: Any,
                 research_store: ResearchStore) -> None:
        self._vault = vault
        self._budget = budget_port
        self._store = research_store
        self._revealed: set[str] = set()
        self.raw_read_count = 0

    def read_raw(self, *, actor: str) -> Any:
        """公开方法永远拒绝；run 使用受限内部引用，不把答案交给调用方。"""
        self._vault.public_read(actor)
        raise AssertionError("unreachable")

    async def run(self, *, frozen_release_id: str, trial_id: str,
                  requested_at: datetime) -> BlindChallengeResult:
        offset = requested_at.utcoffset()
        if requested_at.tzinfo is None or offset is None or offset.total_seconds() != 0:
            raise DataError(detail="blind requested_at 必须是 UTC aware datetime")
        if not frozen_release_id or not trial_id:
            raise DataError(detail="blind runner 只接受 frozen release/trial reference")
        # Quota is consumed before touching the private dataset.
        consumption = await self._budget.reserve_reveal(trial_id=trial_id, requested_at=requested_at)
        interval_key = str(self._vault._raw.get("interval_key", self._vault._raw.get("scope", "blind-window")))
        if interval_key in self._revealed:
            raise DataError(detail="blind interval 已永久 revealed")
        self.raw_read_count += 1
        private_payload = {key: value for key, value in self._vault._raw.items() if key not in {"answer", "raw_rows", "payload"}}
        answer = self._vault._raw.get("answer")
        result_hash = hashlib.sha256(canonical_json({"release": frozen_release_id, "trial": trial_id, "interval": interval_key, "answer": answer, "payload": private_payload}).encode()).hexdigest()
        passed = bool(self._vault._raw.get("passed", False))
        revealed_at = datetime.now(UTC)
        result = BlindChallengeResult(
            challenge_id=f"blind-{hashlib.sha256(f'{frozen_release_id}:{interval_key}'.encode()).hexdigest()[:20]}",
            trial_id=trial_id, result_hash=result_hash, passed=passed,
            revealed_at=revealed_at, budget_consumption_id=consumption.consumption_id,
            revealed_scope=interval_key,
        )
        self._store.append_blind_result(
            result_id=f"{result.challenge_id}:{interval_key}", challenge_id=result.challenge_id,
            trial_id=trial_id, interval_key=interval_key, payload=result,
            created_at=revealed_at,
        )
        self._revealed.add(interval_key)
        return result


__all__ = ["BlindChallengeRunner", "BlindDatasetVault"]
