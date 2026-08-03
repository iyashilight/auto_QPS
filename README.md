# autoQps — 个人加密货币中频量化交易系统

> 个人单机、单用户的加密货币（现货 + 永续合约）中频量化系统。
> 目标：数据 → 研究 → 回测 → 模拟 → 实盘闭环。

## 文档入口

- 架构设计：[加密货币中频AI量化系统架构设计.md](加密货币中频AI量化系统架构设计.md)
- 实现规格：[docs/README.md](docs/README.md)
- 工程规范：[docs/01-项目结构与工程规范.md](docs/01-项目结构与工程规范.md)

## 快速开始

```bash
# 1. 安装依赖（需要 uv）
uv sync

# 2. 查看命令树
qps --help

# 3. 运行测试
uv run pytest

# 4. 静态检查
uv run ruff check src/ tests/
uv run mypy src/qps/
```

## 环境变量

| 变量 | 用途 |
| --- | --- |
| `QPS_MODE` | 运行模式：`paper` / `production` |
| `QPS_DATA_DIR` | 数据目录覆盖 |
| `QPS_BINANCE_API_KEY` | Binance API Key |
| `QPS_BINANCE_API_SECRET` | Binance API Secret |
| `QPS_LOG_LEVEL` | 日志级别覆盖 |

密钥只走环境变量，禁止明文入库。
