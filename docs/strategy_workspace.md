# 策略工作台（Strategy Workspace）

从「写策略」到「实盘跟踪」的一条闭环：**策略版本库 → 回测 → 部署 → 再平衡 → 持仓盈亏**，
全部在本地、可离线、无需券商账户。

```
 /strategy save        /backtest            /deploy                /strategy show
   ┌─────────┐  保存   ┌─────────┐  关联   ┌──────────┐  跟踪   ┌──────────────┐
   │ 策略代码 │ ─────▶ │ 版本+回测 │ ─────▶ │ 实盘持仓  │ ─────▶ │ 实盘 vs 回测  │
   └─────────┘         └─────────┘         └──────────┘         └──────────────┘
                            ▲                    │  rebalance / close
                            └────────────────────┘
```

## 数据模型

| 存储 | 文件 | 内容 |
|------|------|------|
| 策略版本库 `StrategyVault` | `~/.arthera/strategies/vault.db` | 每个策略的版本史、代码、`backtest_result`、`review_result` |
| 持仓账本 `PortfolioLedger` | `~/.arthera/portfolio.db` | 买卖交易、持仓成本、已/未实现盈亏 |

**软关联**：账本的交易表**没有 strategy 列**。一笔交易归属哪个策略，靠它的自由文本
`reason` 字段是否**包含策略名**（不区分大小写、最长名优先）来判定。`/deploy` 会自动写入
`reason="deploy <策略> @<版本>"`，因此部署的持仓能被 `/strategy show` 与 `/portfolio holdings` 关联；
手动 `/journal add buy AAPL 100 185 价值组合` 只要 `reason` 含策略名也同样生效。

共享归属原语：`PortfolioLedger.positions_by_strategy(names)` → `{策略: [持仓...]}`（未归属者归入
`(未归属)` 并置于末尾）。

## 命令生命周期

### 1. 策略版本库
```bash
/strategy save 价值组合 "E/P + S/P 复合"   # 存对话里最后一段策略代码为新版本（自动 v1, v2, …）
/strategy show                           # 所有策略总览：版本数 / 最新 / 回测 Sharpe·收益 / 审查 / 是否部署
/strategy show 价值组合                   # 单策略工作台：版本史 + 关联回测(指标 + equity 火花线) + 实盘 vs 回测
/strategy diff 价值组合 v1 v2            # 版本差异（彩色 diff）
/strategy load 价值组合 v2               # 把某版本代码加载回对话上下文
/strategy review                         # AI 审查 + 静态检测
```

### 2. 部署到实盘
回测结果**不存目标持仓**，因此部署时由你给出要建立的持仓——可按股数，也可按权重×资金折算：

```bash
/deploy 价值组合 AAPL:10 MSFT:5@320          # 按股数（@价格可省，省则取实时价）
/deploy 价值组合 $100000 AAPL:30% MSFT:20%   # 按权重（先给资金，自动按实时价折算股数）
/deploy 价值组合 rebalance AAPL:30% MSFT:20%       # 再平衡【预览】——计算调仓但不落账
/deploy 价值组合 rebalance apply AAPL:30% MSFT:20% # 再平衡【执行】——记录调仓交易
/deploy 价值组合 rebalance equal                   # 目标=当前持仓等权（免手填）
/deploy 价值组合 rebalance like 动量组合           # 目标=另一策略的当前市值权重（免手填）
/deploy 价值组合 close                       # 平掉该策略当前净持仓
/deploy 价值组合                             # 仅显示该策略回测摘要 + 用法
```

token 语法：`SYM:qty[@price]`（股数）或 `SYM:pct%[@price]`（权重）；资金用 `$100000` 或 `cap:100000`。

### 3. 持仓与盈亏
```bash
/portfolio holdings    # 按来源策略分组的持仓看板：逐标的实时浮盈 + 每组小计 + 合计
/journal pnl           # 全部持仓未实现盈亏（实时报价）
/journal realized      # 已实现盈亏（FIFO）
```

## 设计注记（诚实边界）

- **再平衡默认预览**：`rebalance` 不带 `apply` 时只打印调仓计划，不写账本——多笔买卖，先看后做更安全。
- **不做空**：再平衡的卖出数量上限为当前持仓；目标权重为 0 即清空该标的。
- **实盘 vs 回测对比口径不同**：`/strategy show` 把「实盘自部署以来的浮盈」与「回测全程总收益」并列，
  仅作方向参考（已在界面标注「口径不同，仅供参考」），不是严格跟踪误差。
- **目标权重三种来源**：显式 `SYM:pct%`、`equal`（当前持仓等权）、`like <策略>`（对齐另一策略的当前市值权重）。
  回测本身不持久化目标组合，故不直接「对齐到回测组合」；如需此能力，可先把回测目标用 `/deploy` 建成一个参考策略再 `like` 它。

## 相关源码

| 文件 | 作用 |
|------|------|
| `strategy_vault.py` | `StrategyVault`：版本库 + `save_backtest()` / `ai_review_strategy()` |
| `portfolio_ledger.py` | `PortfolioLedger`：交易/持仓/盈亏 + `positions_by_strategy()` |
| `apps/cli/commands/backtest_cmds.py` | `cmd_strategy`（含 `show` 工作台、`_strategy_overview` 总览）、`cmd_deploy`（含 `_parse_deploy_token` / `_rebalance_plan` / `_value_weights`） |
| `apps/cli/commands/portfolio_cmds.py` | `cmd_portfolio`（含 `_portfolio_holdings_by_strategy`）、`cmd_journal` |
| `tests/test_portfolio_ledger.py`, `tests/test_backtest_cmds.py` | 原语单测 |
