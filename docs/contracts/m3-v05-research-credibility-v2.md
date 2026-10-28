# M3 v0.5 可信研究 v2 正式契约

ADR-018 的可执行契约。本文件取代 ADR-017 的 v1 实施契约；旧文件仅保留历史追溯。

## 权威对象与边界

`TrialRegistration` 保存研究的预声明与预期 lineage；`FormalRunArtifact` 保存实际、已验证的
lineage 和输出 hash。两者不得互相替代。

`FormalRunArtifact` 至少包含 `trial_id`、`run_id`、`release_id`、Release archive hash、dataset
manifest hash、feature materialization hash、historical rule hash、cost hash、environment lock、
seed、UTC clock、trades/equity/report hash 和 canonical lineage hash。缺任何 actual 字段、与
registration 预期不一致或 hash 不一致即不得成为 formal result。

```python
class ResearchRunService(Protocol):
    async def register_and_reserve_trial(...) -> TrialRegistration: ...
    async def run_formal(...) -> FormalRunArtifact: ...
    async def rerun(...) -> FormalRunArtifact: ...

class EvidenceReportBuilder(Protocol):
    async def build_from_formal_run(
        self, *, formal_run_artifact_id: str,
    ) -> EvidenceReport: ...
```

`build_from_formal_run` 不得接受 caller supplied lineage。`ResearchStore.append_report` 必须验证
report 的 canonical lineage 与 formal artifact、registration、trial 和 frozen Release 完全匹配。

## 研究层级与报告

Validation 必须使用 UTC 单调推进的 walk-forward 窗口，窗口、purge/embargo、OOS 输入/指标/失败
原因 append-only。Blind 只经本地受限 runner 访问，结果不能回写开发 Registry。报告必须有真实来源的
benchmark、成本压力、容量、不确定性、多重试验调整、failure criteria 与 limitations，并将
`EvidenceQuality`、`ExpectedEdge`、`RiskEstimate` 分开保存。

## 晋升边界

正式证据只能来自 ADR-018 调用图。人工选择记录与 `ReleaseBuilder.build()` 必须验证 report/release
lineage；输出只是一份 M4 Paper candidate，不能自动 approve/deploy/allocate capital。所有研究实体
均 append-only，重启/失败不会改变已存在内容 hash。
