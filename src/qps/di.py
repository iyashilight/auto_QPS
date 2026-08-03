"""依赖组装（模块化单体容器）

本阶段只提供骨架，实际组装在 M0 阶段实现。
所有端口实现通过此模块注册与获取，禁止跨模块直接 import 实现。
"""

from __future__ import annotations


class Container:
    """依赖注入容器（骨架）"""

    _instance: Container | None = None

    def __init__(self) -> None:
        # M0 阶段填充实际端口实现
        pass

    @classmethod
    def get_instance(cls) -> Container:
        """获取单例容器"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """重置容器（测试用）"""
        cls._instance = None
