# ADR-002 工程地基决策（M-1）

## 状态

已接受

## 背景

M-1 阶段建立项目工程地基，需要确定依赖管理选型、目录结构、日志格式、CLI 框架选型等工程约定。

## 决策

### 1. 依赖管理：uv

- **选型**：uv（而非 poetry）
- **理由**：uv 速度更快（Rust 实现），锁文件格式兼容 pip，与 pyproject.toml 标准兼容；个人项目不需要 poetry 的复杂插件生态
- **约束**：锁文件 `uv.lock` 提交入库，保证可复现环境

### 2. 目录结构

- **严格遵循** `docs/01` 第 2 节定义的 monorepo 目录结构
- **核心约束**：`src/qps/core/` 下禁止任何 IO（不 import 网络、文件、数据库）
- **策略分离**：`strategies/` 与 `src/` 物理分离，策略是运行时加载的插件
- **数据隔离**：`data/` 整体 gitignore，所有路径通过配置注入

### 3. 结构化日志格式

- **格式**：JSON lines（每行一条 JSON 记录）
- **统一字段**：`timestamp`（UTC ISO 8601）、`level`、`module`、`message`
- **可选字段**：`event_id`、`strategy_version`、`data`（结构化数据）
- **脱敏**：`api_key`、`api_secret`、`password`、`token`、`credential`、`private_key` 等字段自动脱敏为 `***MASKED***`
- **双模式**：JSON lines（生产/日志文件）+ 纯文本（开发调试）

### 4. CLI 框架：typer

- **选型**：typer（而非 click）
- **理由**：typer 基于 click，提供类型注解驱动的参数定义、自动帮助文档生成、rich 集成；与项目「类型注解必须」的规范一致
- **约束**：CLI 只做参数解析与端口调用，禁止在 CLI 层写业务逻辑

### 5. 构建后端：hatchling

- **选型**：hatchling（而非 setuptools / uv_build）
- **理由**：hatchling 是现代 Python 构建后端，PEP 517/518 兼容，配置简洁；不绑定特定包管理器
- **约束**：包路径 `src/qps/`，入口命令 `qps`

### 6. 静态检查

- **Lint/Format**：ruff（替代 flake8 + isort + black，统一工具链）
- **类型检查**：mypy strict 模式
- **第三方库类型缺失**：polars、duckdb、websockets、yaml 通过 mypy overrides 忽略

## 备选方案

- poetry：功能更全但速度慢，个人项目不需要其复杂特性
- click：更成熟但 typer 提供更好的类型注解体验
- setuptools：传统但配置冗长，hatchling 更现代
- flake8 + black + isort：ruff 统一替代，减少工具链复杂度

## 影响

- 所有开发者必须使用 uv 管理依赖
- 日志必须通过 `qps.infra.logging` 模块输出，禁止直接使用 `print()` 或裸 `logging`
- CLI 命令必须通过 typer 注册，禁止散落脚本
- CI/本地检查必须通过 ruff + mypy 才能提交

## 日期

2026-08-02
