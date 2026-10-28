"""统一 Research Budget 的正式实现。"""

from __future__ import annotations

import json
from decimal import Decimal
from hashlib import sha256
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

from qps.core.constants import ResearchActorType, ResearchScope
from qps.core.errors import DataError
from qps.core.models.foundation import (
    BudgetConsumption,
    CandidateDescriptor,
    ResearchBudget,
    TrialRegistration,
)
from qps.infra.storage.research_db import ResearchStore, canonical_json


class ResearchBudgetManager:
    """在单个 SQLite 事务中解析并预留全部生效 scope。"""

    def __init__(self, store: ResearchStore | Path | str) -> None:
        self.store = store if isinstance(store, ResearchStore) else ResearchStore(store)

    def add_budget(self, budget: ResearchBudget) -> None:
        payload = canonical_json(budget)
        with self.store.connect(immediate=True) as conn:
            row = conn.execute("SELECT payload FROM research_budgets WHERE budget_id = ?", (budget.budget_id,)).fetchone()
            if row is not None:
                if row["payload"] != payload:
                    raise DataError(detail=f"预算内容冲突: {budget.budget_id}")
                return
            conn.execute(
                "INSERT INTO research_budgets(budget_id,scope,scope_ref,period,payload) VALUES(?,?,?,?,?)",
                (budget.budget_id, budget.scope.value, budget.scope_ref, budget.period, payload),
            )

    def _active_budgets(self, conn: Any, *, scope: ResearchScope, refs: set[str], reserved_at: datetime) -> list[ResearchBudget]:
        rows = conn.execute(
            "SELECT payload FROM research_budgets WHERE scope = ? AND scope_ref IN ({})".format(",".join("?" for _ in refs)),
            (scope.value, *sorted(refs)),
        ).fetchall() if refs else []
        budgets = [ResearchBudget.model_validate(json.loads(row["payload"])) for row in rows]
        return [item for item in budgets if item.valid_from <= reserved_at < item.valid_to]

    @staticmethod
    def _scope_refs(registration: TrialRegistration, candidate: CandidateDescriptor, actor_type: ResearchActorType) -> dict[ResearchScope, set[str]]:
        feature_refs = set(candidate.feature_lineage)
        if candidate.feature_family_id:
            feature_refs.add(candidate.feature_family_id)
        return {
            ResearchScope.GLOBAL: {"*", "global", "GLOBAL"},
            ResearchScope.STRATEGY_FAMILY: {registration.strategy_family_id},
            ResearchScope.FEATURE_FAMILY: feature_refs,
            ResearchScope.ACTOR: {actor_type.value, actor_type.name},
        }

    @staticmethod
    def _usage(conn: Any, budget_id: str) -> tuple[int, Decimal, int]:
        rows = conn.execute("SELECT payload FROM research_reservations WHERE budget_id = ?", (budget_id,)).fetchall()
        trials = len(rows)
        compute = Decimal("0")
        reveals = 0
        for row in rows:
            payload = json.loads(row["payload"])
            compute += Decimal(str(payload["compute_cost"]))
            reveals += int(payload.get("blind_reveals", 0))
        return trials, compute, reveals

    async def reserve_trial(
        self, *, registration: TrialRegistration,
        candidate: CandidateDescriptor,
        actor_type: ResearchActorType,
        requested_compute: Decimal,
        blind_reveals: int,
        reserved_at: datetime,
    ) -> tuple[BudgetConsumption, ...]:
        if requested_compute < 0 or blind_reveals < 0:
            raise DataError(detail="预算请求不能为负")
        refs = self._scope_refs(registration, candidate, actor_type)
        with self.store.connect(immediate=True) as conn:
            existing_rows = conn.execute("SELECT payload FROM research_reservations WHERE trial_id = ? ORDER BY budget_id", (registration.trial_id,)).fetchall()
            if existing_rows:
                existing = tuple(BudgetConsumption.model_validate(json.loads(row["payload"])) for row in existing_rows)
                if any(item.candidate_fingerprint != candidate.candidate_fingerprint or item.compute_cost != requested_compute or item.blind_reveals != blind_reveals for item in existing):
                    raise DataError(detail=f"retry 的 trial reservation 参数不一致: {registration.trial_id}")
                return existing

            selected: list[ResearchBudget] = []
            # 对每个配置的 feature family 都计入；没有配置的 scope 不会凭空制造预算。
            for scope, scope_refs in refs.items():
                active = self._active_budgets(conn, scope=scope, refs=scope_refs, reserved_at=reserved_at)
                if scope in {ResearchScope.GLOBAL, ResearchScope.STRATEGY_FAMILY, ResearchScope.ACTOR}:
                    if len(active) != 1:
                        raise DataError(detail=f"{scope.value} 必须解析到唯一 active budget")
                    selected.append(active[0])
                elif active:
                    # 同一 feature family 的 duplicate budget 会扩大统计口径，正式路径拒绝。
                    if len(active) != 1:
                        raise DataError(detail="FEATURE_FAMILY 必须解析到唯一 active budget")
                    selected.append(active[0])

            if len({item.budget_id for item in selected}) != len(selected):
                raise DataError(detail="预算 scope 解析产生重复 budget_id")
            reservations: list[BudgetConsumption] = []
            for budget in selected:
                count, compute, reveals = self._usage(conn, budget.budget_id)
                if count + 1 > budget.max_trials:
                    raise DataError(detail=f"预算 trial 上限已用尽: {budget.budget_id}")
                if compute + requested_compute > budget.max_compute:
                    raise DataError(detail=f"预算 compute 上限已用尽: {budget.budget_id}")
                if reveals + blind_reveals > budget.max_blind_reveals:
                    raise DataError(detail=f"预算 blind reveal 上限已用尽: {budget.budget_id}")
                suffix = sha256(f"{budget.budget_id}:{registration.trial_id}".encode()).hexdigest()[:16]
                reservations.append(BudgetConsumption(
                    consumption_id=f"bc-{suffix}", reservation_id=f"res-{suffix}",
                    budget_id=budget.budget_id, scope=budget.scope, actor_type=actor_type,
                    trial_id=registration.trial_id, candidate_fingerprint=candidate.candidate_fingerprint,
                    compute_cost=requested_compute, blind_reveals=blind_reveals,
                    consumed_at=reserved_at,
                ))
            if not reservations:
                raise DataError(detail="没有可用的正式研究预算 scope")
            self.store.append_reservations(conn, tuple(reservations), reserved_at)
            return tuple(reservations)

    async def finalize_trial(self, *, trial_id: str, terminal_status: Any, reason: str, finished_at: datetime) -> None:
        # Lifecycle event is persisted by ResearchRunService/ResearchStore. This method
        # intentionally does not mutate reservation rows or issue refunds.
        if terminal_status.value not in {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"}:
            raise DataError(detail="finalize_trial 只能写终态")
        if not reason:
            raise DataError(detail="终态必须有 reason")

    async def reserve_reveal(self, *, trial_id: str, requested_at: datetime) -> BudgetConsumption:
        with self.store.connect(immediate=True) as conn:
            rows = conn.execute("SELECT consumption_id,budget_id,payload FROM research_reservations WHERE trial_id = ? ORDER BY budget_id", (trial_id,)).fetchall()
            if not rows:
                raise DataError(detail=f"blind reveal 没有 trial reservation: {trial_id}")
            reveal_rows: list[tuple[str, str, str, str, str, str]] = []
            selected: BudgetConsumption | None = None
            for row in rows:
                item = BudgetConsumption.model_validate(json.loads(row["payload"]))
                used = conn.execute("SELECT COUNT(*) AS n FROM research_reveals WHERE consumption_id = ?", (item.consumption_id,)).fetchone()["n"]
                if used >= item.blind_reveals:
                    raise DataError(detail=f"blind reveal quota 已耗尽: {item.budget_id}")
                reveal_id = f"reveal-{item.consumption_id}-{used + 1}"
                reveal_rows.append((reveal_id, item.consumption_id, item.budget_id, trial_id, canonical_json({"trial_id": trial_id}), requested_at.isoformat()))
                if selected is None:
                    selected = item.model_copy(update={"consumption_id": reveal_id, "reservation_id": item.consumption_id, "blind_reveals": 1, "consumed_at": requested_at})
            self.store.append_reveals(conn, reveal_rows)
            assert selected is not None
            return selected

    async def reserve(self, budget: ResearchBudget, consumption: BudgetConsumption) -> None:
        """兼容旧 API；正式入口不会调用该单预算方法。"""
        self.add_budget(budget)
        with self.store.connect(immediate=True) as conn:
            count, compute, reveals = self._usage(conn, budget.budget_id)
            if count + 1 > budget.max_trials or compute + consumption.compute_cost > budget.max_compute or reveals + consumption.blind_reveals > budget.max_blind_reveals:
                raise DataError(detail=f"预算超额: {budget.budget_id}")
            self.store.append_reservations(conn, (consumption,), consumption.consumed_at)

    async def consume(self, consumption: BudgetConsumption) -> None:
        # A reservation is itself the immutable trial consumption. No second debit.
        with self.store.connect() as conn:
            row = conn.execute("SELECT 1 FROM research_reservations WHERE consumption_id = ?", (consumption.consumption_id,)).fetchone()
        if row is None:
            raise DataError(detail=f"不能消费未预留的预算: {consumption.consumption_id}")

    async def remaining(self, budget_id: str) -> ResearchBudget:
        with self.store.connect() as conn:
            row = conn.execute("SELECT payload FROM research_budgets WHERE budget_id = ?", (budget_id,)).fetchone()
            if row is None:
                raise DataError(detail=f"预算不存在: {budget_id}")
            budget = ResearchBudget.model_validate(json.loads(row["payload"]))
            count, compute, reveals = self._usage(conn, budget_id)
        return budget.model_copy(update={
            "max_trials": max(0, budget.max_trials - count),
            "max_compute": max(Decimal("0"), budget.max_compute - compute),
            "max_blind_reveals": max(0, budget.max_blind_reveals - reveals),
        })


SQLiteResearchBudgetPort = ResearchBudgetManager

__all__ = ["ResearchBudgetManager", "SQLiteResearchBudgetPort"]
