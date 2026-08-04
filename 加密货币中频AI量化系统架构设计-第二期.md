# 个人加密货币中频 AI 量化交易系统架构设计 —— 第二期

> 版本：v0.4-2（v0.4 增量补充；v0.4 保留不变，本文档只写新增/变更部分）
> 前置文档：`加密货币中频AI量化系统架构设计.md`（v0.4），以下简称"一期文档"
> 定位：将一期文档从"可落地的 MVP 交易系统"升级为"完整 AI Quant Research Platform"
> 核心变更：新增 Market Intelligence Layer / Research Layer（含向量库 RAG）/ Alpha Score 评估 / LLM 五级分层 / 数据来源分级 / Onchain Agent / Strategy Lifecycle Feedback / Learning Loop / 存储架构升级（PostgreSQL + pgvector）
> 里程碑：本文档不调整 M0~M7 编号与内容，各新增模块标注"建议落地阶段"

---

## 目录

13. [总体架构升级：从交易系统到研究平台](#13-总体架构升级从交易系统到研究平台)
14. [Market Intelligence Layer（市场理解层）](#14-market-intelligence-layer市场理解层)
15. [Research Layer（研究层）](#15-research-layer研究层)
16. [Alpha Score 评估体系](#16-alpha-score-评估体系)
17. [LLM 延迟五级分层](#17-llm-延迟五级分层)
18. [数据来源分级体系](#18-数据来源分级体系)
19. [Onchain Agent（链上数据 Agent）](#19-onchain-agent链上数据-agent)
20. [Strategy Lifecycle Feedback（策略生命周期反馈闭环）](#20-strategy-lifecycle-feedback策略生命周期反馈闭环)
21. [Learning Loop（学习闭环）](#21-learning-loop学习闭环)
22. [存储架构升级：PostgreSQL + pgvector + Parquet](#22-存储架构升级postgresql--pgvector--parquet)
23. [与一期文档的映射关系](#23-与一期文档的映射关系)
24. [附录：新增领域模型与事件定义](#24-附录新增领域模型与事件定义)

---

## 13. 总体架构升级：从交易系统到研究平台

### 13.1 一期架构回顾

一期文档的架构主线：

```
Data Infrastructure → Strategy Framework → Risk Engine → Execution Engine → Broker
```

这是一条**交易执行链**，偏"交易系统"。一期文档在研究系统（§2.3）中定义了数据分析、特征引擎、回测、优化、实验管理等模块，但它们更像是交易系统的"前置准备"，而非独立的研究闭环。

### 13.2 问题诊断

一期架构的工程成熟度高（交易链设计完整、风控意识强、执行层考虑周全），但**研究系统完整度不足**：

| 缺失 | 影响 |
|------|------|
| 无 Market Intelligence Layer | 策略只能看价格/指标，无法理解"市场当前处于什么状态" |
| 无 Research Memory | AI 每次研究从零开始，无法利用历史研究经验 |
| 无 Alpha Score | 无法量化"为什么相信这个策略"，策略权重靠人工设定 |
| 无 Strategy Lifecycle Feedback | 策略上线后缺少表现监控→降权→暂停→重新研究的闭环 |
| 无 Learning Loop | 系统无法从失败中学习，策略退役后经验丢失 |

### 13.3 二期目标架构

```
                    AI Quant Research Platform
                               │
                ┌──────────────┼──────────────┐
                │              │              │
          Data Layer    Intelligence    Research Layer
                │         Layer              │
                │              │         ┌───┴───┐
                │              │    Strategy    Vector
                │              │    Research    Memory
                │              │    Backtest    (RAG)
                │              │    Optimization
                │              │    Experiment
                │              │         │
                │              │         ▼
                │              │    Strategy Layer
                │              │    (Version / Lifecycle / Validation)
                │              │         │
                │              │         ▼
                │              │    Portfolio Layer
                │              │    (Allocation / Position / Risk Budget)
                │              │         │
                │              │         ▼
                │              │    Execution Layer
                │              │    (Adapter / Order / Broker)
                │              │         │
                └──────────────┼─────────┼──────────┐
                               │         │          │
                               │    Learning Loop    │
                               │    Performance      │
                               │    Failure Analysis │
                               │    Strategy Evolution│
                               │                     │
                               └─────────────────────┘
```

### 13.4 与一期架构的映射

| 二期层级 | 一期文档对应 | 新增内容 |
|---------|------------|---------|
| Data Layer | §2.2 + §4 | 新增 Onchain / News / Macro 数据源分级 |
| Market Intelligence Layer | §2.3.1+§2.3.2（部分） | **全新**：Regime Detection / Event Analysis / Sentiment / Market State |
| Research Layer | §2.3（研究系统） | 新增 Vector Memory（RAG）/ Strategy Research 闭环 |
| Strategy Layer | §2.3.3+§5 | 新增 Alpha Score 评估 / Lifecycle Feedback |
| Portfolio Layer | §2.4.6 | 新增 Alpha Score 驱动的动态权重 |
| Execution Layer | §2.4+§9 | 无变更 |
| Learning Loop | 无 | **全新**：Performance → Alpha Decay → Re-research 闭环 |

### 13.5 设计原则（补充一期 §1.5）

1. **研究闭环优先于交易闭环**：系统的核心价值不是"自动交易"，而是"持续发现、验证、淘汰、调整策略"。交易是研究的出口，不是目的。
2. **结构化查询 vs 语义检索分离**：精确数值查询走 SQL，经验/相似性检索走向量库，两者不互相替代。
3. **Alpha Score 驱动组合决策**：策略权重不是静态配置，而是由 Alpha Score 动态驱动；Alpha Score 本身由多维度评估产出。
4. **LLM 分层约束**：L0/L1 禁止 LLM，L2 可选小模型，L3/L4 允许 LLM；越靠近执行越确定，越靠近研究越智能。
5. **每个数据源必须经过 Raw → Feature → Signal → Confidence 管线**：原始数据不能直接进入策略，必须经过标准化、特征化、信号化、置信度评估。
6. **策略退役是学习机会**：退役策略的经验（失败原因、失效环境、历史表现）必须保留并可供 AI 检索，这是系统自我进化的核心语料。

---

## 14. Market Intelligence Layer（市场理解层）

> 与一期文档关系：一期 §2.3.1（数据分析）和 §2.3.2（特征引擎）提供了数据基础，但缺少"市场状态理解"这一中间层。本层填补 Data Layer 与 Strategy Layer 之间的语义鸿沟。

### 14.1 定位

Market Intelligence Layer 回答的核心问题：**"市场当前处于什么状态？"**

这不是策略该回答的问题，而是策略的**输入**。策略看的不应是"BTC 现在 60000"，而是"BTC 当前处于高波动趋势行情，动量强，资金费率偏高，链上处于分配阶段"。

### 14.2 模块组成

```
Market Intelligence Layer
├── Regime Detection（市场状态识别）
├── Event Analysis（事件分析）
├── Sentiment Engine（情绪引擎）
├── Onchain State（链上状态，见 §19）
└── Market State Aggregator（市场状态聚合器）
```

### 14.3 Regime Detection（市场状态识别）

**输入**：Feature Store 的价格/动量/波动率/成交量特征

**输出**：`MarketRegime` 工件

```json
{
  "regime_id": "regime_20260804T1000",
  "instrument_id": "BTC-USDT-PERP",
  "ts": "2026-08-04T10:00:00Z",
  "regime": "trending_up",
  "confidence": 0.82,
  "volatility_state": "high",
  "volume_state": "above_average",
  "trend_strength": 0.75,
  "duration_estimate_hours": 48,
  "feature_version": "v2.1",
  "data_version": "d20260801"
}
```

**Regime 受控词表**：

| Regime | 含义 | 策略倾向 |
|--------|------|---------|
| `trending_up` | 上涨趋势 | 趋势策略权重提升 |
| `trending_down` | 下跌趋势 | 趋势策略（做空）或减仓 |
| `ranging` | 震荡 | 均值回归/网格策略权重提升 |
| `high_volatility` | 高波动 | 仓位缩减、止损放宽 |
| `crisis` | 危机/恐慌 | 防御模式、降杠杆 |
| `recovery` | 恢复期 | 逐步加仓、观察 |

**实现路径**：
- MVP（M4~M5）：规则版——基于波动率/趋势指标/成交量的阈值判定
- M7+：ML 模型版——HMM / Regime Switching Model / 聚类模型
- LLM 版：Coordinator Agent 综合理解（但 Regime 本身由模型/规则产出，LLM 只做解释）

**关键约束**：
- Regime 判定必须**确定性可复现**：同一 Feature 输入 → 同一 Regime 输出
- Regime 变化触发 `REGIME_CHANGED` 事件，进入事件总线
- Regime 工件不可变，带版本号

### 14.4 Event Analysis（事件分析）

**输入**：NewsEvent（一期 §2.7.4）+ 行情异常事件

**输出**：`MarketEvent` 工件（对 NewsEvent 的市场影响评估）

```json
{
  "event_id": "mevt_001",
  "source_event_id": "event_001",
  "affected_instruments": ["BTC-USDT-PERP", "ETH-USDT-PERP"],
  "market_impact": {
    "direction": "negative",
    "severity": 7,
    "duration_estimate_hours": 24,
    "confidence": 0.85
  },
  "regime_impact": "may_shift_to_crisis",
  "historical_analogues": ["event_2022_05_collapse", "event_2021_05_crackdown"],
  "recommended_actions": ["reduce_exposure", "tighten_stops"],
  "ts": "2026-08-04T10:01:00Z"
}
```

**与一期 §2.7 的关系**：
- 一期 NewsEvent 是"新闻的结构化抽取"（发生了什么）
- 本层 MarketEvent 是"新闻的市场影响评估"（对交易意味着什么）
- NewsEvent → Event Analysis → MarketEvent，是**管线升级**而非替代

**历史类比检索**：Event Analysis 在生成 MarketEvent 时，可查询向量库中的历史事件记忆（见 §15.3），找到相似案例作为参考。这是 RAG 在本层的核心应用。

### 14.5 Sentiment Engine（情绪引擎）

**输入**：新闻情绪（NewsEvent 的 sentiment 字段）+ 社交媒体情绪（后期）+ 资金费率方向 + OI 变化方向

**输出**：`SentimentState` 工件

```json
{
  "sentiment_id": "sent_20260804T1000",
  "instrument_id": "BTC-USDT-PERP",
  "ts": "2026-08-04T10:00:00Z",
  "composite_score": 0.65,
  "components": {
    "news_sentiment": 0.7,
    "funding_rate_direction": 0.8,
    "oi_direction": 0.5,
    "social_sentiment": null
  },
  "confidence": 0.6,
  "trend": "improving"
}
```

**设计要点**：
- 情绪是**辅助信号**，不是主信号；情绪影响 Alpha Score 的 confidence 维度，不直接生成交易意图
- 社交媒体情绪标注为 M7+ 后期，MVP 只用新闻+资金费率+OI
- 情绪合成采用加权平均，权重可配置，各来源带独立置信度

### 14.6 Market State Aggregator（市场状态聚合器）

**职责**：将 Regime / Event / Sentiment / Onchain State 聚合为统一的 `MarketState` 工件，供下游消费。

```json
{
  "state_id": "mkt_20260804T1000",
  "ts": "2026-08-04T10:00:00Z",
  "regime": { "ref": "regime_20260804T1000", "regime": "trending_up", "confidence": 0.82 },
  "events": [
    { "ref": "mevt_001", "severity": 7, "direction": "negative" }
  ],
  "sentiment": { "ref": "sent_20260804T1000", "score": 0.65, "confidence": 0.6 },
  "onchain": { "ref": "onchain_20260804T1000", "state": "distribution", "confidence": 0.5 },
  "overall_risk_posture": "caution",
  "alpha_environment": "moderate"
}
```

**`overall_risk_posture`** 与一期 §8.5 风险模式状态机的关系：
- MarketState 的 `overall_risk_posture` 是**市场侧评估**（建议性）
- 风险模式状态机是**账户侧决策**（强制性）
- 两者独立运行，风险模式状态机可参考 MarketState 但不必须跟随

**`alpha_environment`** 评估当前市场对策略收益的友好程度：
- `favorable`：趋势明确、波动适中、流动性充足 → Alpha 容易获取
- `moderate`：方向不明确、波动中等 → Alpha 需要精选
- `hostile`：高波动/低流动性/事件密集 → Alpha 难以获取，防御为主

**消费方**：
- Strategy Layer：通过 `ctx.regime`（一期已有）+ `ctx.market_state`（新增）注入策略上下文
- Portfolio Layer：Alpha Score 计算的 `market_fit` 维度来源
- Coordinator Agent：战略意图的输入
- Risk Engine：市场级规则的输入

### 14.7 建议落地阶段

| 模块 | 落地阶段 | 说明 |
|------|---------|------|
| Regime Detection（规则版） | M4 | 与风险模式状态机同步实现 |
| Event Analysis（规则版） | M5 | 与新闻模拟器同步实现 |
| Sentiment Engine（基础版） | M5 | 仅新闻+资金费率+OI |
| Market State Aggregator | M5 | 聚合以上输出 |
| Regime Detection（ML 版） | M7+ | HMM/聚类模型 |
| Sentiment Engine（社媒版） | M7+ | 接入 X/Reddit |
| Event Analysis（LLM 版） | M7 | LLM 理解非结构化文本 |

---

## 15. Research Layer（研究层）

> 与一期文档关系：一期 §2.3 定义了研究系统模块（数据分析/特征引擎/策略框架/回测/优化/实验管理/策略注册表），本层在此基础上新增 Vector Memory（RAG）和 Strategy Research 闭环，将研究系统从"工具集"升级为"有记忆的研究平台"。

### 15.1 定位

Research Layer 回答的核心问题：**"如何持续、高效地发现和验证策略，并从历史研究中积累经验？"**

一期研究系统是"无状态"的——每次研究从零开始。二期引入 Vector Memory 后，研究系统拥有了"量化研究员的历史经验"，可以：
- 查询"历史上类似市场环境下哪些策略有效/失效"
- 查询"类似策略的回测结果和失败原因"
- 查询"类似事件的历史影响和恢复路径"

### 15.2 模块组成

```
Research Layer
├── Strategy Research（策略研究，一期 §2.3.3 扩展）
├── Backtest Engine（一期 §2.3.4 + §7，无变更）
├── Optimization（一期 §2.3.5，无变更）
├── Experiment Management（一期 §2.3.6，无变更）
├── Strategy Registry（一期 §2.3.7，无变更）
└── Vector Memory（新增，RAG 研究记忆库）
```

### 15.3 Vector Memory（RAG 研究记忆库）

#### 15.3.1 定位

Vector Memory 是 AI 研究员的**经验记忆库**，不是行情数据库。

| 存什么 | 不存什么 |
|--------|---------|
| 策略实验记录（失败原因、失效环境） | K 线、Tick、指标数值 |
| 市场事件记忆（历史事件+影响+恢复路径） | 实时行情 |
| AI 研究笔记（市场分析、策略评价） | 订单、成交 |
| 策略经验（某策略在什么环境下有效/失效） | 账户余额、持仓 |

**核心原则**：结构化查询走 SQL，经验/相似性检索走向量库，两者互补不替代。

| 查询类型 | 走什么 | 示例 |
|---------|--------|------|
| 精确数值 | SQL（PostgreSQL） | "过去 30 天 BTC 收益率？" |
| 相似研究 | 向量库（pgvector） | "有没有类似 ETF 事件的历史案例？" |
| 策略经验 | 向量库（pgvector） | "以前哪些策略在高波动环境失效？" |
| 交易记录 | SQL（PostgreSQL） | "策略 A 昨天的成交记录？" |

#### 15.3.2 存储模型

```sql
-- 研究记忆表（PostgreSQL + pgvector）
CREATE TABLE research_memory (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type            VARCHAR(50) NOT NULL,  -- strategy_experiment / market_event / research_note / strategy_experience
    title           TEXT NOT NULL,
    content         TEXT NOT NULL,          -- 结构化文本描述
    embedding       vector(1536),           -- pgvector 向量字段
    metadata        JSONB,                  -- 结构化元数据（策略ID/事件ID/时间范围/标的等）
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_ref      VARCHAR(100),           -- 来源引用（实验ID/事件ID/Agent任务ID）
    confidence      DECIMAL(3,2),           -- 记忆置信度
    tags            TEXT[]                  -- 标签（用于过滤+辅助检索）
);

-- 向量索引（IVFFlat，适合几十万级数据）
CREATE INDEX idx_research_memory_embedding ON research_memory
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
```

#### 15.3.3 记忆类型与内容

**1. 策略实验记忆（strategy_experiment）**

```json
{
  "type": "strategy_experiment",
  "title": "EMA 趋势策略 V3 实验记录",
  "content": "策略: EMA Trend V3 | 测试周期: 2020-2026 | 年化收益: 18% | 最大回撤: 25% | 失败环境: 高波动震荡产生大量假突破 | 失败原因: EMA 在震荡行情中频繁交叉导致过度交易 | 建议: 增加 ATR 过滤，波动率低于阈值时暂停信号",
  "metadata": {
    "strategy_id": "ema_trend_v3",
    "version_no": 3,
    "backtest_run_id": "run_20260801_001",
    "test_period": "2020-01-01/2026-07-31",
    "result": "partial_success",
    "failure_regime": ["ranging", "high_volatility"]
  },
  "tags": ["trend", "ema", "high_volatility_failure", "mean_reversion_alternative"]
}
```

**2. 市场事件记忆（market_event）**

```json
{
  "type": "market_event",
  "title": "2024-01 BTC ETF 通过事件",
  "content": "事件: BTC Spot ETF 获批 | 事件前: BTC 上涨 18%（预期交易） | 发布后: 短期回调 10%（买预期卖事实） | 恢复: 2 周后创新高 | 类似模式: 2021-09 BTC 期货 ETF 通过（短期冲高后回调） | 教训: ETF 类事件需区分预期阶段和落地阶段",
  "metadata": {
    "event_type": "regulatory_approval",
    "affected_assets": ["BTC", "ETH"],
    "event_date": "2024-01-10",
    "impact_duration_hours": 336,
    "pattern": "buy_rumor_sell_news"
  },
  "tags": ["etf", "regulatory", "buy_rumor_sell_news", "btc"]
}
```

**3. AI 研究笔记（research_note）**

```json
{
  "type": "research_note",
  "title": "2026-08 BTC 市场分析",
  "content": "当前 BTC 处于减半后周期，历史规律显示减半后 6-12 个月通常有主升浪。但当前宏观环境偏紧（利率高位），与 2020 减半后环境不同。建议: 趋势策略保持但降低仓位，关注链上积累信号",
  "metadata": {
    "author": "market_agent",
    "analysis_date": "2026-08-01",
    "regime_at_time": "trending_up",
    "instruments": ["BTC-USDT-PERP"]
  },
  "tags": ["halving_cycle", "macro_constraint", "trend_cautious"]
}
```

**4. 策略经验记忆（strategy_experience）**

```json
{
  "type": "strategy_experience",
  "title": "均线突破策略在牛市有效、熊市失效",
  "content": "策略类型: 均线突破 | 有效环境: trending_up（胜率 62%，盈亏比 1.8） | 失效环境: ranging（胜率 38%，盈亏比 0.6） | 过渡信号: ATR 收窄 + ADX < 20 → 即将进入震荡 | 建议: Regime 为 ranging 时自动降权或暂停",
  "metadata": {
    "strategy_style": "trend",
    "effective_regimes": ["trending_up", "trending_down"],
    "ineffective_regimes": ["ranging", "high_volatility"],
    "source_experiments": ["run_001", "run_002", "run_003"]
  },
  "tags": ["trend", "ma_breakout", "regime_dependent", "ranging_failure"]
}
```

#### 15.3.4 检索接口

```python
class VectorMemoryPort(Protocol):
    """向量记忆库端口——定义接口，实现为 pgvector"""

    def store(self, memory: ResearchMemory) -> str:
        """存储一条研究记忆，返回 memory_id"""
        ...

    def search(
        self,
        query: str,
        top_k: int = 5,
        memory_type: str | None = None,
        tags: list[str] | None = None,
        min_confidence: float = 0.0,
        metadata_filter: dict | None = None,
    ) -> list[ResearchMemory]:
        """语义检索：query 经 embedding 后在向量库中搜索相似记忆"""
        ...

    def search_by_vector(
        self,
        embedding: list[float],
        top_k: int = 5,
        memory_type: str | None = None,
    ) -> list[ResearchMemory]:
        """向量检索：直接用 embedding 搜索（避免重复 embedding）"""
        ...

    def get_by_id(self, memory_id: str) -> ResearchMemory | None:
        """按 ID 精确获取"""
        ...

    def delete(self, memory_id: str) -> bool:
        """删除记忆（仅用于纠错，正常不删除）"""
        ...
```

#### 15.3.5 RAG 工作流

```
Agent 收到研究任务
       │
       ▼
  1. 构造查询（自然语言描述当前研究问题）
       │
       ▼
  2. Embedding → 向量库 search → Top-K 相似记忆
       │
       ▼
  3. 拼接上下文：当前任务状态 + 检索到的历史经验
       │
       ▼
  4. LLM 推理：综合分析，生成策略/评价/建议
       │
       ▼
  5. 产出工件（策略/实验/评价）→ 同时写入向量库作为新记忆
```

**关键约束**：
- 检索结果必须附带 `confidence` 和 `source_ref`，Agent 不能盲信检索结果
- 写入向量库的内容必须是**结构化文本**（不是原始对话），保证可检索、可审计
- 向量库不是唯一知识来源——Agent 仍需查询 SQL 获取精确数据

#### 15.3.6 Embedding 模型选型

| 方案 | 优点 | 缺点 | 建议 |
|------|------|------|------|
| OpenAI text-embedding-3-small | 质量高、维度灵活 | 需 API 调用、有成本 | M7 默认 |
| 本地模型（BGE-M3 / E5） | 免费、离线、隐私 | 需 GPU/内存、质量略低 | 可选替代 |
| 多语言模型 | 支持中文研究笔记 | 维度较大 | 按需 |

**建议**：定义 `EmbeddingPort` 接口，M7 先用 OpenAI API，后续可切换本地模型。

### 15.4 Strategy Research 闭环

一期研究系统是"工具集"（回测/优化/实验），缺少"研究流程"的编排。二期补充：

```
研究任务触发（定时/事件/人工）
       │
       ▼
  1. Market State 查询 → 当前市场状态
       │
       ▼
  2. Vector Memory 检索 → 历史相似研究/策略经验
       │
       ▼
  3. 策略假设生成（规则/LLM）
       │
       ▼
  4. 脚手架生成 → 策略骨架 + 参数 schema
       │
       ▼
  5. 回测验证 → ReportCard
       │
       ▼
  6. Alpha Score 评估 → 是否值得继续
       │
       ├── Alpha Score < 阈值 → 记录失败经验到 Vector Memory → 结束
       │
       └── Alpha Score ≥ 阈值 → 进入优化
                │
                ▼
           7. 参数优化 + Walk-Forward
                │
                ▼
           8. 样本外验证
                │
                ├── OOS 不通过 → 记录过拟合经验 → 结束
                │
                └── OOS 通过 → 策略进入 Validated → 记录成功经验
```

**与一期 §5 策略生命周期的关系**：本闭环覆盖 Draft → Backtesting → Validated 阶段，是生命周期前半段的"研究流程编排"。Validated 之后的流程（Paper Trading → Production → 退役）由一期 §5 + §20 Lifecycle Feedback 覆盖。

### 15.5 建议落地阶段

| 模块 | 落地阶段 | 说明 |
|------|---------|------|
| Vector Memory（pgvector 基础） | M7 | 与 AI Agent 同步引入 |
| Embedding 服务 | M7 | 先用 OpenAI API |
| Strategy Research 闭环（规则版） | M3~M4 | 脚手架+回测+门禁（一期已有） |
| Strategy Research 闭环（LLM 版） | M7 | LLM 生成策略假设 |
| RAG 检索集成 | M7 | Agent 研究时自动检索历史经验 |

---

## 16. Alpha Score 评估体系

> 与一期文档关系：一期 §3.6 Metrics 定义了回测指标集，§5.3 定义了迁移门禁，但缺少"为什么相信这个策略"的量化评估。Alpha Score 填补这一空白。

### 16.1 定位

Alpha Score 回答的核心问题：**"这个策略/信号在当前市场环境下，值得分配多少资金和信任？"**

没有 Alpha Score 时：
- 策略权重靠人工配置（一期 StrategyAllocation 是静态权重）
- 策略上线后无法量化"信任度变化"
- 组合决策缺少统一的评估语言

有了 Alpha Score 后：
- Portfolio Manager 根据 Alpha Score 动态调整策略权重
- 策略表现下降 → Alpha Score 下降 → 自动降权
- 新策略上线 → Alpha Score 初始评估 → 决定初始权重
- 不同类型策略（趋势/均值回归/事件）可在同一框架下比较

### 16.2 评估维度

Alpha Score 不固定公式，但定义以下评估维度：

| 维度 | 含义 | 数据来源 | 取值范围 |
|------|------|---------|---------|
| **opportunity** | 机会频率——该策略在当前市场环境下产生有效信号的频率 | 回测交易次数/年 + 当前 Regime 下的历史频率 | 0~1 |
| **confidence** | 置信度——对该策略当前有效性的信心 | 回测胜率 + 样本外表现 + Paper/实盘偏差 + 策略运行时长 | 0~1 |
| **stability** | 稳定性——策略表现的时序一致性 | 收益波动率 + 回撤一致性 + Walk-Forward 参数稳定性 + Regime 切换后表现 | 0~1 |
| **market_fit** | 市场适配度——策略与当前市场状态的匹配程度 | 策略有效 Regime vs 当前 Regime + 策略失效 Regime vs 当前 Regime | 0~1 |
| **decay_rate** | 衰减率——策略 Alpha 正在衰减的速度 | 近期表现 vs 历史表现的滑动对比 + 同类策略市场拥挤度指标 | 0~1（0=无衰减，1=完全失效） |
| **risk_cost** | 风险成本——策略占用的风险预算 | 最大回撤 + 波动率 + 资金费率成本 + 强平风险 | 0~1（0=低成本，1=极高风险） |

### 16.3 信号级 Alpha Score

每个交易信号产出时附带 Alpha Score：

```json
{
  "signal_id": "sig_20260804_001",
  "strategy_id": "btc_momentum_v3",
  "instrument_id": "BTC-USDT-PERP",
  "direction": "long",
  "alpha_score": {
    "opportunity": 0.7,
    "confidence": 0.8,
    "stability": 0.65,
    "market_fit": 0.9,
    "decay_rate": 0.15,
    "risk_cost": 0.3
  },
  "composite_score": null,
  "note": "趋势信号强，当前 regime=trending_up 高度适配，衰减率低"
}
```

### 16.4 策略级 Alpha Score

策略级 Alpha Score 是该策略所有信号的综合评估，用于 Portfolio Manager 的权重决策：

```json
{
  "strategy_id": "btc_momentum_v3",
  "version_no": 3,
  "evaluated_at": "2026-08-04T10:00:00Z",
  "alpha_score": {
    "opportunity": 0.6,
    "confidence": 0.75,
    "stability": 0.7,
    "market_fit": 0.85,
    "decay_rate": 0.2,
    "risk_cost": 0.35
  },
  "composite_score": null,
  "trend": "stable",
  "recommended_weight_adjustment": "maintain"
}
```

### 16.5 Composite Score 聚合

Composite Score 是各维度的加权聚合，**公式不固定**，由配置决定：

```yaml
alpha_score_config:
  # 维度权重（可配置，不同策略风格可不同）
  weights:
    opportunity: 0.20
    confidence: 0.25
    stability: 0.20
    market_fit: 0.20
    decay_rate: 0.10
    risk_cost: 0.05  # risk_cost 作为惩罚项，权重可负

  # 聚合方式
  aggregation: "weighted_sum"  # 可选: weighted_sum / geometric / custom

  # decay_rate 惩罚
  decay_penalty: "multiplicative"  # composite * (1 - decay_rate)

  # risk_cost 惩罚
  risk_penalty: "divisive"  # composite / (1 + risk_cost)
```

**默认聚合逻辑**（可被配置覆盖）：

```
raw_score = Σ(weight_i × dimension_i)
adjusted = raw_score × (1 - decay_rate) / (1 + risk_cost)
composite = clamp(adjusted, 0, 1)
```

### 16.6 Alpha Score 驱动 Portfolio Manager

一期 §2.4.6 的 StrategyAllocation 是静态权重。二期升级为 Alpha Score 驱动的动态权重：

```
策略权重调整流程：

1. 定期（每 bar / 每小时 / 每日）计算各策略 Alpha Score
2. 根据 Alpha Score 调整策略权重：
   - composite_score > 0.7 → 维持或提升权重
   - 0.4 < composite_score < 0.7 → 维持权重但收紧额度
   - composite_score < 0.4 → 降低权重
   - composite_score < 0.2 → 暂停策略（进入 Paused）
3. 权重调整幅度受约束：
   - 单次调整不超过 ±20%（防抖）
   - 总权重归一化
   - 最低现金比例保障
4. 权重调整记录写入审计日志
```

**与一期 StrategyAllocation 的关系**：
- 一期静态权重作为**初始值**和**下限**
- Alpha Score 在初始值基础上做动态调整
- 人工可随时覆盖（覆盖记录审计）

### 16.7 Alpha Score 的数据来源

| 维度 | 回测阶段 | Paper 阶段 | 实盘阶段 |
|------|---------|-----------|---------|
| opportunity | 回测交易频率 + Regime 分布 | Paper 信号频率 | 实盘信号频率 |
| confidence | 回测胜率 + OOS 表现 | Paper 胜率 + 回测-Paper 偏差 | 实盘胜率 + 滑点偏差 |
| stability | Walk-Forward 稳定性 | Paper 收益波动 | 实盘收益波动 |
| market_fit | 回测 Regime 分布 vs 当前 | 同左 | 同左 + 实时 Regime |
| decay_rate | 无（初始为 0） | 近期 vs 历史对比 | 滑动窗口表现对比 |
| risk_cost | 回测最大回撤 + 波动率 | Paper 回撤 + 资金费率 | 实盘回撤 + 资金费率 + 强平风险 |

### 16.8 建议落地阶段

| 模块 | 落地阶段 | 说明 |
|------|---------|------|
| Alpha Score 维度定义 | M3 | 与回测指标同步定义 |
| 信号级 Alpha Score（回测） | M3 | 回测产出时附带评分 |
| 策略级 Alpha Score | M4 | 与 Portfolio Manager 同步 |
| Alpha Score 驱动动态权重 | M5 | Paper 阶段验证 |
| Alpha Score 实盘校准 | M6 | 实盘表现反馈修正评分 |

---

## 17. LLM 延迟五级分层

> 与一期文档关系：一期 §1.5.2 和 §2.7 隐含了"交易路径不用 LLM"的原则，但没有显式分层。本节将此原则显式化、量化。

### 17.1 分层定义

| 层级 | 时效 | 技术 | LLM 策略 | 典型模块 |
|------|------|------|---------|---------|
| **L0 实时执行层** | 毫秒 | Go/C++/Rust（未来热点）、Python asyncio | **禁止 LLM** | 行情接收、订单发送、风控检查、成交回报 |
| **L1 策略计算层** | 秒 | Python / NumPy / Polars / Numba | **禁止 LLM** | 指标计算、信号生成、特征计算、Regime 判定（规则/ML） |
| **L2 市场状态层** | 分钟 | ML 模型（LightGBM/HMM/聚类）+ 规则 | **可选小模型** | 异常检测、市场状态更新、情绪合成、链上状态判定 |
| **L3 研究分析层** | 小时 | LLM（Claude/GPT） | **允许 LLM** | 新闻理解、研究报告、策略评价、回测分析、Agent 研究 |
| **L4 策略进化层** | 天/周 | LLM Agent + 回测 | **允许 LLM Agent** | 策略创新、参数研究、系统优化、经验总结、Meta Strategy |

### 17.2 分层约束

**硬约束（代码层面强制）**：

1. **L0/L1 禁止 LLM 调用**：交易关键路径（行情→策略→风控→执行）的代码中不得出现任何 LLM API 调用。静态检查可验证。
2. **L2 的 LLM 调用必须超时可控**：如果 L2 使用小模型，调用必须有超时（如 5s），超时后回退到规则默认值。
3. **L3/L4 的 LLM 输出不得直接进入交易链路**：LLM 产出只能是工件（结构化事件/意图/报告），必须经过 Portfolio Manager → Risk Engine → Execution Engine 才能影响交易。

**软约束（设计原则）**：

4. **越靠近执行越确定**：L0/L1 的输出必须是确定性的（相同输入→相同输出），L2 允许概率性但需有回退，L3/L4 允许不确定性但需可审计。
5. **LLM 结果必须可降级**：任何使用 LLM 的模块必须有"LLM 不可用时的规则回退"。
6. **成本意识**：L3/L4 的 LLM 调用应有成本预算和频率限制。

### 17.3 各层与模块的映射

```
L0 ── Market Data Gateway / Execution Engine / Broker Adapter
L1 ── Feature Engine / Strategy Runtime / Signal Engine / Risk Engine (规则部分)
L2 ── Regime Detection (ML版) / Sentiment Engine / Onchain State / 异常检测
L3 ── News Agent (LLM版) / Market Agent (LLM版) / Backtest Agent / Strategy Agent
L4 ── Coordinator Agent / Optimization Agent / Risk Agent / Strategy Evolution
```

### 17.4 跨层交互规则

| 交互 | 规则 |
|------|------|
| L4 → L3 | L4 产出策略假设 → L3 验证（回测/评价） |
| L3 → L2 | L3 产出事件理解 → L2 更新市场状态 |
| L2 → L1 | L2 产出 Regime/Sentiment → L1 策略通过 ctx.market_state 读取 |
| L1 → L0 | L1 产出 TradingIntent → L0 执行 |
| L4 → L1 | **禁止直接交互**。L4 只能通过 PortfolioIntent → Portfolio Manager → Risk Engine → Execution |
| L3 → L0 | **禁止直接交互**。L3 产出只能通过事件/工件间接影响 |

### 17.5 延迟预算（补充一期 §6.5）

| 路径 | 预算 | 说明 |
|------|------|------|
| L0: 行情→策略回调 | < 50ms | 一期已有 |
| L0: 信号→风控→订单 | < 10ms | 一期已有 |
| L1: 特征计算（单标的单 bar） | < 5ms | 新增 |
| L2: Regime 更新 | < 30s | 新增，每 bar 或每分钟 |
| L2: 异常检测 | < 10s | 新增 |
| L3: 新闻理解 | < 60s | 新增，单条新闻 |
| L3: 策略评价 | < 5min | 新增，含回测 |
| L4: 策略研究 | < 1h | 新增，含多轮回测 |
| L4: 策略进化 | < 1day | 新增，含参数搜索 |

### 17.6 建议落地阶段

| 层级 | 落地阶段 | 说明 |
|------|---------|------|
| L0/L1 | M0~M4 | 一期已覆盖 |
| L2（规则版） | M4~M5 | Regime/Sentiment 规则实现 |
| L2（ML 版） | M7+ | HMM/聚类模型 |
| L3 | M7 | LLM Agent 接入 |
| L4 | M7+ | Coordinator + Strategy Evolution |

---

## 18. 数据来源分级体系

> 与一期文档关系：一期 §2.2.1 确认"MVP 不购买付费数据，使用交易所公开 API"。本节定义完整的数据来源分级，作为未来扩展的路线图。

### 18.1 分级定义

| Level | 数据类别 | 代表数据 | 可靠性 | 接入阶段 |
|-------|---------|---------|--------|---------|
| **L0 基础市场数据** | OHLCV K 线、成交量 | Binance/OKX REST+WS | ★★★★★ | M1（一期已落地） |
| **L1 交易所行为数据** | 资金费率、持仓量、标记价格 | Binance Funding/OI API | ★★★★☆ | M1（一期已落地） |
| **L2 衍生品深度数据** | 订单簿快照、买卖压力、CVD | Binance Depth API | ★★★★☆ | M3+（回测滑点建模） |
| **L3 链上数据** | 交易所净流入、巨鲸行为、稳定币供应、活跃地址 | Glassnode/CryptoQuant API | ★★★☆☆ | M7+（见 §19） |
| **L4 新闻数据** | 官方公告、RSS 新闻 | Binance 公告/CoinDesk RSS | ★★★☆☆ | M5/M6（一期已规划） |
| **L5 宏观数据** | 美元指数、利率、流动性指标 | FRED/Yahoo Finance | ★★☆☆☆ | M7+ |
| **L6 社交媒体** | X/Twitter、Reddit、Telegram | 各平台 API | ★☆☆☆☆ | M7+ 后期 |

### 18.2 数据管线约束

**所有数据源必须经过统一管线**：

```
Raw Data（原始落盘，不可变）
    │
    ▼
Clean / Normalize（清洗归一化，统一 schema）
    │
    ▼
Feature（特征化，版本化）
    │
    ▼
Signal（信号化，带置信度）
    │
    ▼
Confidence（置信度评估）
    │
    ▼
进入策略 / Market Intelligence / Alpha Score
```

**关键规则**：
1. **原始数据不能直接进入策略**：任何数据源必须经过 Feature → Signal → Confidence 管线
2. **每个数据源带质量报告**：缺失率、延迟、异常值比例
3. **低可靠性数据源的 Confidence 上限受限**：L5/L6 数据的 confidence 上限不超过 0.5（配置可调）
4. **数据源故障不得阻塞交易路径**：L3~L6 数据源不可用时，系统降级运行（只用 L0~L2），不暂停交易

### 18.3 各级数据对 Alpha Score 的贡献

| 数据级别 | 对 Alpha Score 维度的贡献 | 权重上限 |
|---------|------------------------|---------|
| L0~L1 | opportunity / confidence / stability / risk_cost 的主要来源 | 无上限 |
| L2 | risk_cost（滑点/深度）+ confidence（成交质量） | 20% |
| L3 | market_fit（链上状态影响 Regime 判定） | 10% |
| L4 | confidence（事件影响评估）+ market_fit | 10% |
| L5~L6 | confidence（辅助） | 5% |

### 18.4 建议落地阶段

| Level | 落地阶段 | 说明 |
|-------|---------|------|
| L0~L1 | M1 | 一期已落地 |
| L2 | M3+ | 回测滑点建模需要 |
| L3 | M7+ | 见 §19 Onchain Agent |
| L4 | M5/M6 | 一期已规划 |
| L5 | M7+ | 宏观数据接入 |
| L6 | M7+ 后期 | 社交媒体情绪 |

---

## 19. Onchain Agent（链上数据 Agent）

> 与一期文档关系：一期完全没有链上数据设计。本节为全新模块。

### 19.1 定位

加密货币市场最大的独特性是**链上透明**——股票市场没有这个数据维度。Onchain Agent 利用这一独特性，产出链上市场状态，影响 Regime 判定和 Alpha Score。

**核心原则**：Onchain Agent 产出的是**市场状态**，不是买卖信号。

```
正确：Onchain Agent → Onchain State → Market State → Regime/Alpha Score
错误：Onchain Agent → Buy/Sell Signal
```

### 19.2 角色定义

| 属性 | 定义 |
|------|------|
| 输入 | 链上数据（交易所净流入/流出、巨鲸行为、稳定币供应、活跃地址） |
| 输出 | `OnchainState` 工件 |
| 权限 | 只读数据；产出市场状态工件；**无交易权、无信号权** |
| 激活方式 | 定时调度（每 5m~1h） |
| LLM 分层 | L2（规则/小模型判定链上状态） |

### 19.3 OnchainState 工件

```json
{
  "onchain_id": "onchain_20260804T1000",
  "ts": "2026-08-04T10:00:00Z",
  "state": "distribution",
  "confidence": 0.55,
  "components": {
    "exchange_net_flow": {
      "direction": "inflow",
      "magnitude": "moderate",
      "value_btc": 12500,
      "interpretation": "BTC 流入交易所增加，潜在卖压"
    },
    "whale_activity": {
      "pattern": "large_transfer_to_exchange",
      "count_24h": 3,
      "interpretation": "巨鲸向交易所转移，通常预示卖出"
    },
    "stablecoin_supply": {
      "trend": "stable",
      "change_7d_pct": 0.5,
      "interpretation": "稳定币供应稳定，无大量资金进出"
    },
    "active_addresses": {
      "trend": "declining",
      "change_7d_pct": -3.2,
      "interpretation": "活跃地址减少，市场参与度下降"
    }
  },
  "data_source": "glassnode",
  "data_quality": "partial",
  "data_version": "d20260801"
}
```

**State 受控词表**：

| State | 含义 | 对 Regime 的影响 |
|-------|------|-----------------|
| `accumulation` | 链上积累阶段——BTC 流出交易所、巨鲸囤币 | 支持 trending_up |
| `neutral` | 链上中性——无明显方向 | 无影响 |
| `distribution` | 链上分配阶段——BTC 流入交易所、巨鲸卖出 | 支持 trending_down / caution |
| `panic` | 链上恐慌——大量流入交易所、稳定币流出 | 支持 crisis |

### 19.4 数据源

| 数据 | 来源 | 说明 | 接入阶段 |
|------|------|------|---------|
| 交易所净流入/流出 | Glassnode / CryptoQuant API | 核心指标 | M7+ |
| 巨鲸转账 | Whale Alert / Glassnode | 辅助指标 | M7+ |
| 稳定币供应 | Glassnode | 资金面指标 | M7+ |
| 活跃地址 | Glassnode / Dune | 网络活动指标 | M7+ |
| Gas 使用 | Etherscan / Glassnode | 辅助 | M7+ 后期 |

**数据源约束**：
- 链上数据 API 多为付费（Glassnode/CryptoQuant），M7+ 需评估成本
- 免费替代：Whale Alert（有限免费）、Etherscan API（免费但限频）
- 链上数据延迟通常 5~15 分钟，不适合秒级决策
- 链上数据解释困难（巨鲸转账可能是钱包迁移而非卖出），confidence 上限受限

### 19.5 Onchain Agent 与 Market Intelligence Layer 的关系

```
Onchain Agent ──OnchainState──┐
                               ├──► Market State Aggregator ──► MarketState
Regime Detection ──Regime─────┘         │
Event Analysis ──Event────────┘         ├──► Alpha Score (market_fit 维度)
Sentiment Engine ──Sentiment──┘         └──► Coordinator Agent (输入)
```

OnchainState 是 MarketState 的一个组件，不独立影响交易决策。

### 19.6 建议落地阶段

| 模块 | 落地阶段 | 说明 |
|------|---------|------|
| OnchainState schema 定义 | M4 | 与 MarketState 同步定义 |
| Onchain Agent（规则版，模拟数据） | M5 | 用模拟数据验证管线 |
| Onchain Agent（真实数据） | M7+ | 接入 Glassnode/CryptoQuant |
| Onchain Agent（LLM 增强版） | M7+ | LLM 辅助链上数据解释 |

---

## 20. Strategy Lifecycle Feedback（策略生命周期反馈闭环）

> 与一期文档关系：一期 §5 定义了策略生命周期状态机（9 状态+门禁），但缺少"上线后表现监控→降权→暂停→重新研究"的反馈闭环。本节补充。

### 20.1 问题

一期策略生命周期是**单向门禁**：

```
Draft → Backtesting → Validated → Paper → Production → (Paused/Deprecated)
```

缺少**反馈回路**：

- 策略在 Production 表现下降 → 如何检测？如何量化？
- 策略被暂停后 → 如何决定是重新研究还是退役？
- 策略退役后 → 失败经验如何保留供未来参考？

### 20.2 完整闭环

```
策略生成
    │
    ▼
回测验证 ──── 不通过 ──→ 记录失败经验 ──→ Vector Memory
    │ 通过
    ▼
模拟验证 ──── 不通过 ──→ 记录偏差原因 ──→ Vector Memory
    │ 通过
    ▼
实盘运行
    │
    ▼
表现监控（Performance Monitor）
    │
    ├── Alpha Score 稳定 ──→ 维持运行
    │
    ├── Alpha Score 下降 ──→ 降权（Portfolio Manager 调整）
    │                         │
    │                         ├── Alpha Score 恢复 ──→ 恢复权重
    │                         │
    │                         └── Alpha Score 持续下降 ──→ 暂停策略
    │                                                      │
    │                                                      ▼
    │                                               失败分析（Failure Analysis）
    │                                                      │
    │                                              ┌───────┴───────┐
    │                                              │               │
    │                                         可修复           不可修复
    │                                              │               │
    │                                              ▼               ▼
    │                                        重新研究          退役
    │                                        (Fork 新版本)    (Deprecated)
    │                                              │               │
    │                                              ▼               ▼
    │                                        回测验证          记录退役经验
    │                                              │          → Vector Memory
    │                                              └──→ (回到回测验证)
    │
    └── 风控触发 ──→ 暂停/禁用 ──→ 人工诊断 ──→ 恢复/退役
```

### 20.3 Performance Monitor（表现监控）

**职责**：持续监控运行中策略的实盘表现，与回测预期对比，产出 Alpha Score 变化趋势。

**监控指标**：

| 指标 | 计算方式 | 告警阈值 |
|------|---------|---------|
| 滚动胜率 | 最近 N 笔交易的胜率 | 低于回测胜率 20%+ |
| 滚动盈亏比 | 最近 N 笔交易的平均盈亏比 | 低于回测盈亏比 30%+ |
| 滑点偏差 | 实际成交价 vs 信号价 | 超过回测滑点假设 2x |
| 信号频率偏差 | 实际信号频率 vs 回测频率 | 偏差 > 50% |
| Alpha Score 趋势 | composite_score 的滑动平均斜率 | 连续 3 个评估周期下降 |
| 回撤偏差 | 实际回撤 vs 回测回撤 | 超过回测最大回撤 80% |
| 资金费率成本 | 实际 funding_pnl vs 回测 funding_pnl | 偏差 > 30% |

**输出事件**：

| 事件 | 触发条件 | 消费方 |
|------|---------|--------|
| `ALPHA_DECAY_DETECTED` | Alpha Score 连续下降 | Portfolio Manager（降权） |
| `PERFORMANCE_DEVIATION` | 实盘表现偏离回测预期 | Monitoring（告警） |
| `STRATEGY_UNDERPERFORMING` | 多项指标同时恶化 | Lifecycle Manager（暂停建议） |

### 20.4 Alpha Decay Detection（Alpha 衰减检测）

Alpha 衰减是策略从有效走向失效的过程。检测方法：

```
1. 计算策略的 Alpha Score 时间序列
2. 滑动窗口回归：最近 W 个评估周期的 Alpha Score 斜率
3. 斜率 < -threshold → Alpha 正在衰减
4. 衰减速度分级：
   - 缓慢衰减（斜率 -0.01~-0.05/周期）→ 降权 10~20%
   - 中等衰减（斜率 -0.05~-0.1/周期）→ 降权 30~50%
   - 快速衰减（斜率 < -0.1/周期）→ 暂停策略
5. 衰减原因分析（辅助）：
   - Regime 变化导致？→ 检查 market_fit 维度
   - 市场拥挤度增加？→ 检查同类策略表现
   - 数据质量下降？→ 检查数据质量报告
   - 交易所规则变化？→ 检查 NewsEvent
```

### 20.5 Failure Analysis（失败分析）

策略被暂停后，进入失败分析：

```json
{
  "analysis_id": "fa_20260804_001",
  "strategy_id": "btc_momentum_v3",
  "version_no": 3,
  "failure_type": "alpha_decay",
  "failure_details": {
    "alpha_score_at_pause": 0.25,
    "alpha_score_at_start": 0.78,
    "decay_duration_days": 45,
    "primary_cause": "regime_change",
    "regime_at_failure": "ranging",
    "effective_regimes": ["trending_up", "trending_down"],
    "secondary_cause": "increased_competition",
    "evidence": ["rolling_win_rate_dropped", "signal_frequency_increased"]
  },
  "recommendation": "re_research",
  "re_research_direction": "增加 Regime 过滤，ranging 时暂停信号；或转向均值回归策略",
  "vector_memory_stored": true,
  "memory_id": "mem_fa_20260804_001"
}
```

**失败类型分类**：

| 失败类型 | 含义 | 处理 |
|---------|------|------|
| `alpha_decay` | Alpha 逐渐衰减 | 重新研究（可能可修复） |
| `regime_mismatch` | 市场环境变化导致策略失效 | 等待 Regime 恢复或重新研究 |
| `structural_change` | 市场结构/规则变化 | 退役（通常不可修复） |
| `overfitting_revealed` | 实盘暴露过拟合 | 退役 + 重新研究新版本 |
| `risk_breach` | 风控触发 | 人工诊断 |
| `data_quality` | 数据问题导致 | 修复数据后重新评估 |

### 20.6 重新研究 vs 退役决策

| 条件 | 决策 |
|------|------|
| 失败类型 = regime_mismatch 且 Regime 可能恢复 | 暂停等待，不退役 |
| 失败类型 = alpha_decay 且有明确修复方向 | Fork 新版本重新研究 |
| 失败类型 = structural_change | 退役 |
| 失败类型 = overfitting_revealed | 退役，新版本必须用不同方法论 |
| 连续 3 个版本都失败 | 退役该策略风格，记录经验 |

### 20.7 与一期 §5 策略生命周期的集成

一期状态机新增以下迁移：

| 迁移 | 触发 | 证据 |
|------|------|------|
| Production → Paused | Alpha Decay 检测（自动）或风控触发 | AlphaDecayReport / RiskEvent |
| Paused → Backtesting（Fork 新版本） | 失败分析建议 re_research + 人工确认 | FailureAnalysis + 人工审批 |
| Paused → Deprecated | 失败分析建议退役 + 人工确认 | FailureAnalysis + 人工审批 |
| Paused → Production | Alpha Score 恢复 + 人工确认 | AlphaScoreTrend + 人工审批 |

**关键约束**：
- Production → Paused 可由系统自动触发（Alpha Decay / 风控）
- Paused → 任何状态的恢复必须人工确认
- 所有失败分析结果写入 Vector Memory

### 20.8 建议落地阶段

| 模块 | 落地阶段 | 说明 |
|------|---------|------|
| Performance Monitor（基础指标） | M5 | Paper 阶段开始监控 |
| Alpha Decay Detection | M5 | 与 Alpha Score 同步 |
| Failure Analysis（规则版） | M6 | 实盘准备阶段 |
| Failure Analysis（LLM 版） | M7 | LLM 辅助失败原因分析 |
| Lifecycle Feedback 完整闭环 | M6 | 实盘运行后闭环 |

---

## 21. Learning Loop（学习闭环）

> 与一期文档关系：一期没有"系统级学习"概念。本节为全新设计。

### 21.1 定位

Learning Loop 回答的核心问题：**"系统如何从所有策略的成功和失败中持续学习，使整体研究能力不断提升？"**

这不是单个策略的反馈（§20 已覆盖），而是**系统级**的学习：

- 跨策略的经验积累
- 研究方法论的自优化
- 市场认知的持续更新

### 21.2 学习闭环架构

```
┌─────────────────────────────────────────────────────────────┐
│                      Learning Loop                           │
│                                                              │
│  ┌──────────┐    ┌──────────────┐    ┌──────────────────┐  │
│  │Performance│───►│Failure       │───►│Strategy          │  │
│  │Monitor    │    │Analysis      │    │Evolution         │  │
│  └──────────┘    └──────────────┘    └──────────────────┘  │
│       │                  │                    │              │
│       │                  ▼                    ▼              │
│       │          ┌──────────────┐    ┌──────────────────┐  │
│       │          │Experience    │    │Research           │  │
│       │          │Repository    │    │Methodology        │  │
│       │          │(Vector Memory│    │Update             │  │
│       │          │ + SQL)       │    │                   │  │
│       │          └──────────────┘    └──────────────────┘  │
│       │                  │                    │              │
│       └──────────────────┼────────────────────┘              │
│                          │                                   │
│                          ▼                                   │
│                   ┌──────────────┐                           │
│                   │System        │                           │
│                   │Self-Assessment│                           │
│                   └──────────────┘                           │
└─────────────────────────────────────────────────────────────┘
```

### 21.3 Experience Repository（经验仓库）

经验仓库是 Vector Memory + SQL 的联合存储，记录系统运行的所有经验：

**SQL 部分**（PostgreSQL）——精确查询：

| 表 | 内容 |
|----|------|
| `strategy_lifecycle_log` | 策略状态迁移记录（何时上线、何时暂停、为何退役） |
| `alpha_score_history` | 每个策略的 Alpha Score 时间序列 |
| `failure_analysis_log` | 失败分析记录 |
| `research_outcome_log` | 研究任务结果（成功/失败/部分成功） |

**向量部分**（pgvector）——语义检索：

| 记忆类型 | 内容 |
|---------|------|
| `strategy_experience` | 策略在什么环境下有效/失效 |
| `market_event` | 历史事件及其影响 |
| `research_note` | AI 研究笔记 |
| `failure_lesson` | 失败教训 |
| `methodology_update` | 研究方法论更新 |

### 21.4 Strategy Evolution（策略进化）

策略进化不是"修改旧策略"，而是**基于失败经验生成新策略假设**：

```
1. 失败分析产出 FailureAnalysis
2. 查询 Vector Memory：类似失败的历史案例
3. 查询 Vector Memory：在当前 Regime 下有效的策略类型
4. LLM 综合分析：失败原因 + 历史经验 + 当前市场 → 新策略假设
5. 新策略假设 → Strategy Research 闭环（§15.4）→ 验证
```

**示例**：

```
失败策略: EMA Trend V3
失败原因: ranging 环境下频繁假突破
历史经验: 均值回归策略在 ranging 环境表现好
当前 Regime: ranging (持续 2 周)
LLM 建议: Fork V4，增加 ATR 过滤 + ranging 时切换为均值回归逻辑
→ 新策略: EMA Trend V4 (regime-adaptive)
```

### 21.5 Research Methodology Update（研究方法论更新）

系统不仅学习"什么策略有效"，还学习"如何研究更有效"：

| 方法论维度 | 学习内容 |
|-----------|---------|
| 回测配置 | 哪些回测参数组合最容易过拟合？→ 调整默认配置 |
| 特征选择 | 哪些特征在多个策略中反复出现？→ 优先纳入特征库 |
| 参数搜索 | 哪些参数范围最常产出有效策略？→ 收窄搜索空间 |
| 门禁标准 | 当前门禁是否过松/过严？→ 根据实盘反馈调整 |
| Regime 判定 | 当前 Regime 分类是否合理？→ 根据策略表现反馈调整 |

**产出**：`MethodologyUpdate` 工件

```json
{
  "update_id": "meth_20260804_001",
  "dimension": "backtest_config",
  "finding": "walk-forward 窗口 < 6 个月的策略实盘表现显著差于 ≥ 6 个月",
  "evidence": ["run_001", "run_002", "run_003", "fa_001", "fa_002"],
  "recommendation": "将默认 walk-forward 最小窗口从 3 个月调整为 6 个月",
  "confidence": 0.75,
  "status": "pending_review"
}
```

**关键约束**：方法论更新**不自动生效**，必须人工审核后才能修改系统默认配置。

### 21.6 System Self-Assessment（系统自评估）

定期（每周/每月）对系统整体运行状况进行评估：

| 评估维度 | 指标 |
|---------|------|
| 策略组合表现 | 组合 Alpha Score 趋势、组合夏普比率、组合最大回撤 |
| 研究效率 | 策略从 Draft 到 Production 的平均周期、研究成功率 |
| 预测能力 | Alpha Score 预测策略实盘表现的准确率 |
| 学习效果 | Vector Memory 检索对研究成功率的提升 |
| 风控效果 | 风控拦截的误伤率、漏过率 |

**产出**：`SystemAssessment` 工件，供人工审查和系统优化。

### 21.7 建议落地阶段

| 模块 | 落地阶段 | 说明 |
|------|---------|------|
| Experience Repository（SQL 部分） | M5 | 与 Performance Monitor 同步 |
| Experience Repository（Vector 部分） | M7 | 与 Vector Memory 同步 |
| Strategy Evolution（规则版） | M7 | 基于失败分析自动 Fork |
| Strategy Evolution（LLM 版） | M7+ | LLM 生成新策略假设 |
| Research Methodology Update | M7+ | 需要足够的历史数据 |
| System Self-Assessment | M7+ | 需要足够的运行周期 |

---

## 22. 存储架构升级：PostgreSQL + pgvector + Parquet

> 与一期文档关系：一期 §4.1 选型 SQLite + Parquet + 内存缓存。二期升级为 PostgreSQL + pgvector + Parquet。SQLite 作为一期实验阶段存储，在迁移窗口完成数据迁移。

### 22.1 升级目标

| 用途 | 一期选型 | 二期选型 | 理由 |
|------|---------|---------|------|
| 历史行情（Raw/Normalized/Feature） | Parquet + DuckDB | **Parquet + DuckDB（不变）** | 列存压缩、扫描快，分析型数据不需要事务 |
| 元数据/实验/策略注册表 | SQLite | **PostgreSQL** | AI 研究阶段大量实验记录/策略版本/参数组合/Agent 任务/embedding，SQLite 并发和扩展能力有限 |
| AI 研究记忆 | 无 | **PostgreSQL + pgvector** | 向量检索 + 结构化查询在同一数据库，个人项目最合适 |
| 实盘运行状态 | SQLite + 内存 | **PostgreSQL + 内存** | 事务一致性更强，崩溃恢复更可靠 |
| 配置 | YAML + 环境变量 | **YAML + 环境变量（不变）** | — |

### 22.2 最终存储架构

```
                 Quant System
                      │
        ┌─────────────┼─────────────┐
        │             │             │
   Analytical      Research       State
   Data            Data           Data
        │             │             │
   Parquet +      PostgreSQL +   PostgreSQL +
   DuckDB         pgvector       内存缓存
        │             │             │
   历史行情        策略/实验/      订单/持仓/
   特征数据        Agent/记忆      账户/风控
```

**各存储职责**：

| 存储 | 存什么 | 不存什么 |
|------|--------|---------|
| **Parquet + DuckDB** | K 线、Tick、Funding、OI、OrderBook、Feature | 业务实体、运行状态 |
| **PostgreSQL** | 策略、策略版本、参数、实验、回测结果、部署、风控规则、审计日志、Agent 任务 | 大规模时序数据 |
| **PostgreSQL + pgvector** | 研究记忆（embedding + 元数据） | 行情数据、交易数据 |
| **内存缓存** | 当前订单、持仓、账户、风控状态 | 历史数据 |
| **YAML** | 系统配置、策略参数 schema | 运行时状态 |

### 22.3 PostgreSQL Schema（新增/迁移部分）

一期 SQLite 中的表迁移到 PostgreSQL，同时新增 AI 研究相关表：

**从 SQLite 迁移的表**（结构基本不变，利用 PostgreSQL 特性增强）：

```sql
-- 策略相关（一期 §4.5）
strategies, strategy_versions, strategy_params, strategy_allocations,
deployments, experiments, report_cards, risk_rules, risk_events, audit_log, tasks, artifacts

-- 新增：利用 PostgreSQL JSONB 替代 SQLite JSON
-- 新增：利用 PostgreSQL UUID 替代自增 ID
-- 新增：利用 PostgreSQL 事务和并发能力
```

**新增表**：

```sql
-- 研究记忆（§15.3）
research_memory (id UUID, type, title, content, embedding vector(1536), metadata JSONB, ...)

-- Alpha Score 历史（§16）
alpha_score_history (id UUID, strategy_id, version_no, evaluated_at, dimensions JSONB, composite_score, trend, ...)

-- 策略生命周期日志（§20）
strategy_lifecycle_log (id UUID, strategy_id, from_state, to_state, trigger, evidence JSONB, ...)

-- 失败分析（§20）
failure_analysis (id UUID, strategy_id, version_no, failure_type, details JSONB, recommendation, ...)

-- 方法论更新（§21）
methodology_updates (id UUID, dimension, finding, evidence JSONB, recommendation, confidence, status, ...)

-- 系统评估（§21）
system_assessments (id UUID, period_start, period_end, dimensions JSONB, overall_score, ...)

-- Agent 任务（§2.7 扩展）
agent_tasks (id UUID, agent_type, trigger_type, input_artifacts JSONB, output_artifacts JSONB, status, ...)

-- 市场状态快照（§14）
market_state_snapshots (id UUID, ts, regime JSONB, events JSONB, sentiment JSONB, onchain JSONB, overall_risk_posture, alpha_environment, ...)

-- 链上状态快照（§19）
onchain_state_snapshots (id UUID, ts, state, components JSONB, confidence, data_source, ...)
```

### 22.4 迁移策略

**原则**：SQLite 作为一期实验阶段存储，在迁移窗口完成数据迁移；不是"推倒重写"，而是"正式升级事务存储层"。

**迁移步骤**：

```
1. PostgreSQL 部署（Docker 单容器，个人本机）
2. Schema 创建（一期表结构 + 新增表）
3. 数据迁移脚本（SQLite → PostgreSQL，保留原始 ID 和时间戳）
4. 双写验证期（同时写 SQLite 和 PostgreSQL，对比一致性）
5. 切换读取到 PostgreSQL
6. 停止写 SQLite
7. SQLite 文件归档（不删除，作为历史备份）
```

**迁移时机**：建议在 M4（组合与风控）完成后、M5（模拟交易）开始前执行，此时系统已有完整数据但尚未进入实盘。

**回退方案**：如果 PostgreSQL 出现问题，可回退到 SQLite（双写期间保证一致性）。

### 22.5 pgvector 配置

```sql
-- 启用 pgvector 扩展
CREATE EXTENSION IF NOT EXISTS vector;

-- Embedding 维度：1536（OpenAI text-embedding-3-small）
-- 索引类型：IVFFlat（适合几十万级数据）
-- 当数据超过百万级时，考虑切换到 HNSW 索引

-- 向量相似度搜索示例
SELECT id, title, content,
       1 - (embedding <=> $query_vector) AS similarity
FROM research_memory
WHERE type = 'strategy_experience'
  AND confidence >= 0.5
ORDER BY embedding <=> $query_vector
LIMIT 5;
```

### 22.6 建议落地阶段

| 模块 | 落地阶段 | 说明 |
|------|---------|------|
| PostgreSQL 部署 + Schema 创建 | M4 结束 | 迁移窗口 |
| SQLite → PostgreSQL 数据迁移 | M4 结束 | 迁移窗口 |
| pgvector 扩展 + research_memory 表 | M7 | 与 Vector Memory 同步 |
| 双写验证期 | M4 结束 → M5 开始 | 1~2 周 |
| 完全切换到 PostgreSQL | M5 开始 | 迁移完成 |

---

## 23. 与一期文档的映射关系

### 23.1 章节映射

| 二期章节 | 一期章节 | 关系 |
|---------|---------|------|
| §13 总体架构升级 | §1 总体架构 | 扩展：新增 Intelligence/Research/Learning 层 |
| §14 Market Intelligence | §2.3.1+§2.3.2 | 扩展：从"数据分析"升级为"市场理解" |
| §15 Research Layer | §2.3 | 扩展：新增 Vector Memory + Research 闭环 |
| §16 Alpha Score | §3.6+§5.3 | 扩展：新增评估体系，驱动动态权重 |
| §17 LLM 五级分层 | §1.5.2+§2.7 | 显式化：将隐含原则量化为五级分层 |
| §18 数据来源分级 | §2.2.1 | 扩展：从"免费 API"到完整分级体系 |
| §19 Onchain Agent | 无 | 新增 |
| §20 Lifecycle Feedback | §5 | 扩展：新增反馈闭环 |
| §21 Learning Loop | 无 | 新增 |
| §22 存储升级 | §4 | 变更：SQLite → PostgreSQL + pgvector |

### 23.2 领域模型映射

| 二期新增模型 | 一期对应 | 说明 |
|------------|---------|------|
| MarketRegime | 无 | 新增：市场状态工件 |
| MarketEvent | NewsEvent（§2.7.4） | 扩展：从"新闻事件"到"市场影响评估" |
| SentimentState | 无 | 新增：情绪状态工件 |
| OnchainState | 无 | 新增：链上状态工件 |
| MarketState | 无 | 新增：聚合市场状态工件 |
| AlphaScore | 无 | 新增：策略/信号评估 |
| ResearchMemory | 无 | 新增：向量记忆 |
| FailureAnalysis | 无 | 新增：失败分析 |
| MethodologyUpdate | 无 | 新增：方法论更新 |
| SystemAssessment | 无 | 新增：系统自评估 |

### 23.3 事件映射

| 二期新增事件 | 一期对应 | 说明 |
|------------|---------|------|
| `REGIME_CHANGED` | 无 | 新增：Regime 变化广播 |
| `ALPHA_DECAY_DETECTED` | 无 | 新增：Alpha 衰减检测 |
| `PERFORMANCE_DEVIATION` | 无 | 新增：实盘表现偏离 |
| `STRATEGY_UNDERPERFORMING` | 无 | 新增：策略表现恶化 |
| `ONCHAIN_STATE_CHANGED` | 无 | 新增：链上状态变化 |
| `METHODOLOGY_UPDATE_PROPOSED` | 无 | 新增：方法论更新建议 |

### 23.4 不变更的一期设计

以下一期设计在二期中**完全保留，不做修改**：

- §1.3 三层 Adapter 架构
- §2.4 交易系统模块（Market Data Gateway / Strategy Runtime / Signal Engine / Risk Engine / Execution Engine / Portfolio Manager / Monitoring）
- §2.5 Broker 层
- §2.6 公共设施
- §2.7 事件驱动多 Agent 架构（News/Market/Coordinator）
- §3 核心领域模型（Instrument/Bar/Tick/TradingIntent/Order/Trade/Account/Position 等）
- §6 交易流程设计
- §7 回测系统设计（双引擎 + 成本模型）
- §8 风控设计（四级规则 + 风险模式状态机）
- §9 Broker 抽象设计
- §10 MVP 里程碑（M0~M7 编号和内容不变）

---

## 24. 附录：新增领域模型与事件定义

### 24.1 新增领域模型

#### MarketRegime

| 字段 | 类型 | 说明 |
|------|------|------|
| regime_id | string | 唯一 ID |
| instrument_id | string | 标的 |
| ts | timestamp(UTC) | 评估时间 |
| regime | enum | trending_up / trending_down / ranging / high_volatility / crisis / recovery |
| confidence | decimal(0~1) | 置信度 |
| volatility_state | enum | low / normal / high / extreme |
| volume_state | enum | below_average / average / above_average / abnormal |
| trend_strength | decimal(0~1) | 趋势强度 |
| duration_estimate_hours | int | 预计持续时间 |
| feature_version | string | 特征版本 |
| data_version | string | 数据版本 |

#### MarketState

| 字段 | 类型 | 说明 |
|------|------|------|
| state_id | string | 唯一 ID |
| ts | timestamp(UTC) | 聚合时间 |
| regime | object{ref, regime, confidence} | 引用 MarketRegime |
| events | array{ref, severity, direction} | 引用 MarketEvent 列表 |
| sentiment | object{ref, score, confidence} | 引用 SentimentState |
| onchain | object{ref, state, confidence} | 引用 OnchainState |
| overall_risk_posture | enum | normal / caution / defensive / halt |
| alpha_environment | enum | favorable / moderate / hostile |

#### AlphaScore

| 字段 | 类型 | 说明 |
|------|------|------|
| score_id | string | 唯一 ID |
| strategy_id | string | 策略 ID |
| version_no | int | 策略版本 |
| evaluated_at | timestamp(UTC) | 评估时间 |
| opportunity | decimal(0~1) | 机会频率 |
| confidence | decimal(0~1) | 置信度 |
| stability | decimal(0~1) | 稳定性 |
| market_fit | decimal(0~1) | 市场适配度 |
| decay_rate | decimal(0~1) | 衰减率 |
| risk_cost | decimal(0~1) | 风险成本 |
| composite_score | decimal(0~1) | 综合评分（可 null，公式可配置） |
| trend | enum | improving / stable / declining |
| recommended_weight_adjustment | enum | increase / maintain / reduce / pause |

#### ResearchMemory

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 唯一 ID |
| type | enum | strategy_experiment / market_event / research_note / strategy_experience / failure_lesson / methodology_update |
| title | text | 标题 |
| content | text | 结构化文本描述 |
| embedding | vector(1536) | pgvector 向量 |
| metadata | JSONB | 结构化元数据 |
| created_at | timestamptz | 创建时间 |
| source_ref | string | 来源引用 |
| confidence | decimal(0~1) | 记忆置信度 |
| tags | text[] | 标签 |

#### FailureAnalysis

| 字段 | 类型 | 说明 |
|------|------|------|
| analysis_id | string | 唯一 ID |
| strategy_id | string | 策略 ID |
| version_no | int | 策略版本 |
| failure_type | enum | alpha_decay / regime_mismatch / structural_change / overfitting_revealed / risk_breach / data_quality |
| failure_details | JSONB | 失败详情 |
| recommendation | enum | re_research / deprecate / wait |
| re_research_direction | text | 重新研究方向（可 null） |
| vector_memory_stored | boolean | 是否已存入向量库 |
| memory_id | string | 向量库记忆 ID（可 null） |

#### OnchainState

| 字段 | 类型 | 说明 |
|------|------|------|
| onchain_id | string | 唯一 ID |
| ts | timestamp(UTC) | 评估时间 |
| state | enum | accumulation / neutral / distribution / panic |
| confidence | decimal(0~1) | 置信度 |
| components | JSONB | 各链上指标详情 |
| data_source | string | 数据来源 |
| data_quality | enum | complete / partial / unavailable |
| data_version | string | 数据版本 |

### 24.2 新增事件定义

| 事件类型 | Payload | 优先级 | 消费方 |
|---------|---------|--------|--------|
| `REGIME_CHANGED` | {instrument_id, old_regime, new_regime, confidence} | 高 | Strategy Runtime / Portfolio Manager / Coordinator |
| `ALPHA_DECAY_DETECTED` | {strategy_id, alpha_score, decay_rate, recommendation} | 高 | Portfolio Manager / Lifecycle Manager |
| `PERFORMANCE_DEVIATION` | {strategy_id, metric, expected, actual, deviation_pct} | 中 | Monitoring / Lifecycle Manager |
| `STRATEGY_UNDERPERFORMING` | {strategy_id, failing_metrics[], alpha_score} | 高 | Lifecycle Manager / Monitoring |
| `ONCHAIN_STATE_CHANGED` | {old_state, new_state, confidence} | 中 | Market State Aggregator |
| `METHODOLOGY_UPDATE_PROPOSED` | {dimension, finding, recommendation, confidence} | 低 | 人工审核 |
| `MARKET_STATE_UPDATED` | {state_id, regime, risk_posture, alpha_environment} | 高 | Strategy Runtime / Portfolio Manager / Coordinator |

### 24.3 新增端口定义

| 端口 | 方法 | 说明 |
|------|------|------|
| `VectorMemoryPort` | store / search / search_by_vector / get_by_id / delete | 向量记忆库（§15.3.4） |
| `EmbeddingPort` | embed(text) → vector | Embedding 服务（§15.3.6） |
| `RegimeDetectionPort` | detect(features) → MarketRegime | Regime 检测（§14.3） |
| `AlphaScorePort` | evaluate(strategy_id, context) → AlphaScore | Alpha Score 评估（§16） |
| `OnchainDataPort` | get_state(instrument_id) → OnchainState | 链上数据（§19.4） |
| `PerformanceMonitorPort` | get_metrics(strategy_id) → PerformanceMetrics | 表现监控（§20.3） |

---

*本文档为 v0.4-2 增量补充，与 v0.4 共同构成完整架构设计。一期文档（v0.4）定义了交易系统的工程基础，二期文档（v0.4-2）补充了研究平台的核心能力。两者不是替代关系，而是上下层关系：v0.4 是"可落地的 MVP 交易系统"，v0.4-2 是"完整 AI Quant Research Platform"的架构蓝图。*
