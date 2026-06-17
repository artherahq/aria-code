# Changelog

All notable changes to Aria Code are documented here.

---

## [4.1.0] — 2026-06-17

### Added

**UI: Bloomberg Terminal design system**
- New `apps/cli/prompts/ui.py` — Bloomberg-style design constants, CSS generator (`get_ui_css_base()`), and LLM system prompt for generating high-quality HTML dashboards
- New `/ui <description>` command — generates Bloomberg Terminal-style HTML on demand (dark: `#000000`/amber, light: `#FFFFFF`/brown; `border-radius: 0` everywhere; IBM Plex Mono for all numbers; `prefers-color-scheme` auto-switch with no JS)
- New `dashboard_generator.py` — complete rewrite using Bloomberg design; flat grid cards, ALL CAPS section headers, correct price formatter (no scientific notation)

**UX: Tool call transparency (Claude Code-style)**
- `_print_tool_done(tool, elapsed_ms, success)` — prints `✓ action (42ms)` after each tool completes; `✗` on failure
- `_print_phase(label)` — Bloomberg-style phase dividers (`── Reading diff ────────`) for multi-step operations
- Per-turn cost display — cloud turns now append `$0.0089` to the turn metadata line (only when tokens > 0 and provider is cloud)
- `/review` shows diff statistics before LLM analysis: file count, `+N −N` lines

**Per-user global context**
- `~/.arthera/ARIA.md` — global user profile file, auto-injected into every session as the lowest-priority context layer; project-level `ARIA.md` overrides it
- `/memory profile` — new subcommand to manage the global file: `show`, `add <text>`, `clear`
- Example: `/memory profile add 我主要交易A股，偏好技术分析，风险承受能力中等`

**Quantitative engine**
- 5 new MCP quantitative tools integrated into the CLI tool registry
- Citadel/Jane Street-style quant engine: 5 modules (factor model, risk decomposition, portfolio optimizer, execution simulator, performance attribution)
- Dynamic market scanning for long-term analysis + 4 weekly report enhancements
- Short-term dynamic market pool + A-share limit-up (涨停) prediction model

### Changed

**Slash command consolidation**
- Reduced from ~150 slash commands to 83 focused ones — removed all commands that the LLM can handle naturally through conversation
- `/help` restructured: section 1 shows natural language examples, section 2 shows commands by category
- Startup banner `try` hints now show natural language examples instead of slash commands

**LLM routing & capability awareness**
- System prompt updated: LLM now knows it can call `yfinance`/`akshare` for real-time prices (not say "I don't have real-time data"), generate Bloomberg HTML when asked for dashboards, and read `~/.arthera/portfolio.db` for portfolio queries
- Removed references to deleted commands (`/quote`, `/analyze`, `/football`) from system prompt

**`/memory` command**
- Added `profile` subcommand (see above)
- Updated usage hint to show all subcommands including `profile`

### Fixed

- **xtquant URL pollution** — `xtquant` library printed its documentation URL to stdout on import; now suppressed via stdout redirect in `brokers/cn/xtquant_broker.py`
- **NASDAQ price scientific notation** — `2.638e+04` displayed for index values ≥10,000; fixed `_price_str()` to use `{price:,.0f}` for values ≥10,000
- **Screener price labeling** — `现价` changed to `昨收(qfq)` to accurately reflect the data cutoff; no longer implies real-time data

---

## [4.0.1] — 2026-06-10

### Changed

- npm postinstall: improved Python detection, Xcode CLT auto-install, Homebrew auto-install
- `bootstrap.sh` added — single command for fresh macOS/Linux setup with no prerequisites
- `install.sh` hardened — Windows PowerShell fallback path, venv repair logic
- npm `repair` script: `npm explore -g aria-code -- npm run repair`

### Fixed

- npm postinstall failing on macOS systems without Xcode Command Line Tools
- Python 3.12 path detection on Apple Silicon Homebrew layout

---

## [4.0.0] — 2026-05-28

### Added

- **19+ cloud LLM providers** — OpenAI, Anthropic, DeepSeek, Qwen, Gemini, Mistral, Grok, and more; unified provider routing with automatic fallback
- **Feishu multi-user relay** — enterprise Feishu bot with per-user context isolation; relay server + client wizard
- **Telegram integration** — bot mode with `/start`, `/help`, inline keyboard; same agent backend
- **MCP server support** — connect any MCP server; tools appear automatically in the CLI tool registry
- **Broker integration** — XTQuant (迅投) for CN markets; CCXT for crypto; unified `BrokerBase` interface
- **Financial agent teams** (`/team`) — multi-agent analysis with specialist roles (technical, fundamental, risk, macro); synthesis + confidence score
- **Quantitative backtesting** (`/backtest`, `/wf`) — momentum, SMA-cross, breakout strategies; walk-forward validation; HTML reports
- **A-share market tools** — northbound flow, limit-up pool (涨停板), sector rotation, margin data via akshare
- **ML signal injection** — auto-detected stock queries inject ML confidence signals into LLM context
- **Extended thinking** — DeepSeek-R1, QwQ, claude-3-7-sonnet thinking mode; live token counter during reasoning
- **Bloomberg-inspired terminal UI** — Rich-based layout with robot mascot, status panel, tab completion, arrow-select pickers
- **Auto memory** — facts mentioned in conversation captured to `ARIA.md` via `memory_manager`
- **Walk-forward engine** — rolling / anchored / expanding window; out-of-sample performance breakdown
- **Crypto module** — Binance real-time prices, funding rates, OI, portfolio; CCXT multi-exchange
- **Prediction tracking** — `/accuracy` shows model hit rate vs live prices; DPO training data auto-generated

### Changed

- CLI rewritten from single-file script to modular architecture (`apps/cli/`, `runtime/`, `agents/`, `brokers/`, `ui/`)
- Tool calling upgraded — parallel tool execution, JSON hook system (`PreToolUse`, `PostToolUse`, `ResponseDone`)
- Permission system — per-tool allow/deny with session-level memory; `safe` / `balanced` / `workspace-write` policies

### Fixed

- Ollama streaming echo bug in dumb terminals — batch render mode accumulates tokens, renders Markdown once at end
- LaTeX buffering across token boundaries — `\frac` split across two tokens no longer leaks raw LaTeX to output

---

## Architecture

Aria Code follows an **open core** model:

| Layer | Status | Rationale |
|---|---|---|
| CLI framework (`aria_cli.py`, `runtime/`, `ui/`) | Open source (MIT) | Trust through transparency; community contributions |
| Tool calling & agent loop | Open source (MIT) | Auditable for financial use cases |
| SKILLS / plugin system | Open source (MIT) | Ecosystem growth |
| Real-time A-share data pipeline | Proprietary service | Data quality is the moat, not the code |
| ML signal model weights | Proprietary | Alpha-generating; not distributable |
| Broker API integration secrets | Proprietary | Credential management |

This mirrors the Bloomberg Terminal model: the terminal software could theoretically be replicated, but the data infrastructure cannot.
