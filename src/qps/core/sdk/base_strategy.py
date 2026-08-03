"""策略基类（见 docs/04 第 3 节）

策略类必须继承 BaseStrategy，实现 initialize / on_bar / on_tick / on_funding / on_oi 回调。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from qps.core.sdk.context import StrategyContext


class BaseStrategy(ABC):
    """策略基类

    策略开发者继承此类并实现回调方法：
    - initialize: 初始化，读取 ctx.params 与静态数据
    - on_bar: 每个 K 线回调（核心）
    - on_tick: 可选逐笔回调
    - on_funding: 可选资金费率回调
    - on_oi: 可选持仓量回调

    行为约束：
    1. 纯计算：回调内禁止网络、文件、进程、线程、time.sleep
    2. 无副作用持久化：跨回调状态必须通过 ctx.state
    3. 确定性：相同输入 + 相同参数产生相同意图序列
    4. 输出唯一通道：通过 ctx.emit_intent(...) 输出意图
    5. 只读市场数据：ctx 提供的行情/特征/持仓都是只读视图
    6. 参数只读：ctx.params 不可变
    """

    @abstractmethod
    def initialize(self, ctx: StrategyContext) -> None:
        """初始化策略

        Args:
            ctx: 策略上下文，提供参数、行情、特征等能力

        说明：
        - 读取 ctx.params 获取参数快照
        - 可进行静态计算（如预计算指标常数）
        - 禁止 IO 操作
        """
        ...

    @abstractmethod
    def on_bar(self, ctx: StrategyContext, bar: Any) -> None:
        """K 线回调

        Args:
            ctx: 策略上下文
            bar: K 线数据（Bar 模型）

        语义：
        - 回测：bar 收盘后触发
        - 实盘：聚合完成时触发（只对已收盘 K 线触发）
        - M5 前补入 is_closed 字段约定，本阶段在文档中预留该语义

        说明：
        - 这是策略的核心回调，大多数信号在此产生
        - 通过 ctx.emit_intent(...) 输出交易意图
        """
        ...

    def on_tick(self, ctx: StrategyContext, tick: Any) -> None:
        """逐笔成交回调（可选）

        Args:
            ctx: 策略上下文
            tick: 逐笔成交数据（Tick 模型）

        说明：
        - 默认不实现，需要精细处理的策略可实现
        - 高频场景慎用（可能产生大量回调）
        """
        pass

    def on_funding(self, ctx: StrategyContext, funding: Any) -> None:
        """资金费率回调（可选）

        Args:
            ctx: 策略上下文
            funding: 资金费率数据（FundingRate 模型）

        说明：
        - 永续策略建议实现
        - 可用于调整仓位或风险敞口
        """
        pass

    def on_oi(self, ctx: StrategyContext, oi: Any) -> None:
        """持仓量回调（可选）

        Args:
            ctx: 策略上下文
            oi: 持仓量数据（OpenInterest 模型）

        说明：
        - 可用于检测异常持仓变化
        """
        pass
