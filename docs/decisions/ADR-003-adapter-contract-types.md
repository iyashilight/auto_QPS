## 状态
已接受

## 背景
docs/03 接口契约声明了 `ExchangeCalendar` 和 `AdapterStatus` 两个返回类型，但代码中对应端口方法（`DataProviderPort.calendar()`、`MarketAdapterPort.status()`、`ExchangeAdapterPort.status()`、`BrokerAdapterPort.status()`）返回 `dict[str, Any]`，导致契约漂移——文档与代码签名不一致，调用方无法获得类型安全保证。

## 决策
1. 新增 `ExchangeCalendar`、`CalendarBreak`（instrument.py）和 `AdapterStatus`（新建 adapter.py）三个 Pydantic v2 frozen 模型；
2. 四个端口方法返回类型从 `dict[str, Any]` 改为强类型：
   - `DataProviderPort.calendar()` → `list[ExchangeCalendar]`
   - `MarketAdapterPort.status()` → `AdapterStatus`
   - `ExchangeAdapterPort.status()` → `AdapterStatus`
   - `BrokerAdapterPort.status()` → `AdapterStatus`
3. `ExchangeCalendar` 为每日期条目，`calendar()` 返回列表按日期升序；
4. `AdapterStatus` 为适配器连接状态快照，字段 `{connected, last_heartbeat, reconnect_count, latency_ms}`；
5. 其余 `dict[str, Any]` 返回类型（如 OrderRequest、ExecutionStatus、JobSpec 等）属于后续里程碑（M4/M5）的契约细化，不在本次范围。

## 影响
- 端口调用方获得编译时类型检查，IDE 自动补全和重构支持；
- 端口实现方需返回符合模型约束的强类型实例，而非任意 dict；
- 新增 adapter.py 模型文件，docs/02 和 docs/03 同步更新。

## 备选方案
1. **保持 dict[str, Any]**：灵活但无类型安全，契约漂移持续存在——否决。
2. **TypedDict**：提供类型提示但无运行时校验，与项目 Pydantic 惯例不一致——否决。
3. **Dataclass**：不支持 frozen + field_validator 组合，与项目 Pydantic v2 惯例不一致——否决。

## 日期
2026-08-03
