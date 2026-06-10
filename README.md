<p align="center">
  <img src="https://img.shields.io/badge/Aria_Code-v3.0-6366f1?style=for-the-badge&logo=terminal&logoColor=white" alt="version"/>
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="python"/>
  <img src="https://img.shields.io/badge/Ollama-Local_LLM-black?style=for-the-badge&logo=llama&logoColor=white" alt="ollama"/>
  <img src="https://img.shields.io/badge/License-MIT-22c55e?style=for-the-badge" alt="license"/>
</p>

<h1 align="center">Aria Code</h1>
<p align="center"><b>AI-powered financial terminal for the command line</b><br>
Runs fully offline with Ollama · Connects to any LLM API · Built for investors & quant researchers</p>

---

## What is Aria Code?

Aria Code is a terminal-first AI agent that understands **financial analysis, quantitative research, and coding workflows**. Think of it as Claude Code or Codex — but with deep finance domain knowledge built in.

```
$ aria-code -p "什么是DCF估值？帮我列出公式和关键假设"

  DCF（折现现金流）模型
  ──────────────────────────────
  核心公式

  ▶ V = Σ(t=1→n) FCF_t ÷ (1 + WACC)^t  +  TV ÷ (1 + WACC)^n

  变量说明
   V      企业当前估值
   FCF_t  第 t 期自由现金流
   WACC   加权平均资本成本（8%–15%）
   TV     终值（Gordon 增长模型）
   n      预测期年数（通常 5–10 年）
  ...
  1.8s · 412 tokens · 68 t/s · qwen2.5-coder:7b (local)
```

---

## Features

- **🦙 100% local mode** — works offline with [Ollama](https://ollama.ai); no API key required
- **📊 Financial intelligence** — DCF, WACC, PE/PB/ROE, Sharpe ratio, Kelly criterion, Black-Scholes, and 30+ built-in financial formulas
- **📈 Live market data** — real-time quotes via yfinance (free), akshare for A-shares, ccxt for crypto
- **🔍 Quant research tools** — `/backtest`, `/signal`, `/regime`, `/kelly`, `/factor`, `/portfolio`
- **🤖 Multi-provider LLM** — Ollama, Claude, OpenAI, DeepSeek, Gemini, DashScope (auto-fallback chain)
- **🔌 MCP support** — connect any [Model Context Protocol](https://modelcontextprotocol.io) server
- **💻 Rich terminal UI** — spinner, tables, formula rendering, color-coded output
- **📁 File awareness** — read local code/data files, generate reports, write analysis to disk
- **🌍 Bilingual** — responds in Chinese or English based on your prompt

---

## Quick Start

### Option 1: One-line install (recommended)

```bash
git clone https://github.com/Cinsoul/aria-code.git
cd aria-code
./install.sh
```

This creates a virtual environment and installs `aria-code` to `~/.local/bin/aria-code`.

Make sure `~/.local/bin` is in your PATH:
```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc
```

### Option 2: Run directly

```bash
git clone https://github.com/Cinsoul/aria-code.git
cd aria-code
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 aria_cli.py
```

### Setup Ollama (local LLM — no API key needed)

```bash
# Install Ollama (macOS / Linux)
curl -fsSL https://ollama.ai/install.sh | sh

# Pull a recommended model
ollama pull qwen2.5-coder:7b      # Best balance of speed & quality (~4.7GB)
# or
ollama pull deepseek-r1:7b        # Better reasoning for complex quant tasks
# or
ollama pull llama3.2:3b           # Fastest, smallest (~2GB)
```

Then just run `aria-code` — it auto-detects Ollama.

### Setup Cloud API (optional)

```bash
cp .env.example .env
# Edit .env and add any of:
# ANTHROPIC_API_KEY, OPENAI_API_KEY, DEEPSEEK_API_KEY, etc.
```

---

## Usage

### Interactive REPL

```bash
aria-code              # Start interactive session
aria-code --resume     # Resume last session
```

### Single Prompt Mode

```bash
aria-code -p "Analyze AAPL momentum"
aria-code -p "什么是夏普比率？给出公式" 
aria-code -p "Compare PE PB ROE for BABA vs JD, use a table"
```

### Built-in Commands

```bash
# Market data
aria-code quote AAPL MSFT TSLA          # Real-time quotes
aria-code quote 000001.SZ 600519.SH     # A-share quotes

# Slash commands (inside REPL)
/quote NVDA                  # Quick quote
/backtest momentum SPY       # Run backtest
/signal TSLA                 # Technical signal analysis
/regime                      # Market regime detection
/kelly AAPL 0.6 2.0          # Kelly criterion position sizing
/factor PE PB ROE            # Factor screening
/portfolio AAPL MSFT GOOGL   # Portfolio optimization
/dcf                         # DCF valuation template
/screen                      # Stock screener
/news AAPL                   # Latest news
```

---

## Slash Commands Reference

| Command | Description |
|---------|-------------|
| `/help` | Show all available commands |
| `/tools` | List enabled tools |
| `/quote <symbol>` | Real-time price quote |
| `/signal <symbol>` | Technical analysis signal |
| `/backtest <strategy> <symbol>` | Backtest a strategy |
| `/regime` | Current market regime (bull/bear/neutral) |
| `/kelly <symbol> <win_rate> <odds>` | Kelly criterion sizing |
| `/factor <factors...>` | Multi-factor analysis |
| `/portfolio <symbols...>` | Portfolio analysis & optimization |
| `/dcf` | DCF valuation template |
| `/screen` | Stock screener with filters |
| `/news <symbol>` | Latest news headlines |
| `/model` | Show current LLM & switch model |
| `/provider` | List/switch LLM providers |
| `/cloud set <url>` | Connect to self-hosted backend |
| `/status` | System status & health check |
| `/export` | Export conversation to file |
| `/clear` | Clear conversation history |
| `/resume` | Resume last saved session |

---

## Configuration

Aria Code stores settings in `~/.arthera/config.json`. You can also use a project-level `.ariarc` file:

```json
// .ariarc (in your project directory)
{
  "model": "qwen2.5-coder:7b",
  "thinking_mode": false,
  "tools": ["read_file", "write_file", "bash", "web_search"]
}
```

### LLM Provider Priority

Aria Code tries providers in this order (first available wins):

```
Aria Cloud → Anthropic → OpenAI → DeepSeek → Gemini → DashScope → Ollama (local)
```

Set `ARIA_MODEL=ollama/qwen2.5-coder:7b` to force local mode.

---

## Market Data Sources

| Source | Coverage | Key Required |
|--------|----------|-------------|
| **yfinance** | US, HK, Global stocks, ETFs, Forex | No (free) |
| **akshare** | A-shares (CN), indices, futures, news | No (free) |
| **ccxt** | 100+ crypto exchanges | No (free tier) |
| Finnhub | US real-time quotes, earnings | Optional |
| Alpha Vantage | US historical + fundamentals | Optional |
| FRED | US macroeconomic data | Optional |
| Tushare | A-share historical + financials | Optional (free token) |

---

## MCP Integration

Connect external tools via [Model Context Protocol](https://modelcontextprotocol.io):

```json
// config/mcp_servers.example.json → copy to config/mcp_servers.json
{
  "servers": [
    {
      "name": "filesystem",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/your/project"]
    },
    {
      "name": "brave-search",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-brave-search"],
      "env": { "BRAVE_API_KEY": "your-key" }
    }
  ]
}
```

---

## Requirements

- **Python 3.10+**
- **[Ollama](https://ollama.ai)** (for local mode — highly recommended)
- 4GB+ RAM (8GB+ recommended for 7B models)
- macOS / Linux / Windows (WSL2)

All Python dependencies are in `requirements.txt`. Key packages:
- `rich` — terminal rendering
- `prompt_toolkit` — interactive REPL
- `yfinance`, `akshare`, `ccxt` — market data
- `pandas`, `numpy`, `scipy` — data processing

---

## Architecture

```
aria-code/
├── aria_cli.py           # Main CLI entry point & REPL (15k lines)
├── local_finance_tools.py # Built-in financial calculators (DCF, WACC, etc.)
├── finance_formulas.py   # LaTeX formula → plain text renderer
├── market_data_client.py # Unified market data interface
├── mcp_client.py         # MCP protocol client
├── model_capability.py   # LLM capability detection & routing
├── strategy_vault.py     # Built-in quant strategies
├── intent_classifier.py  # Query intent routing
│
├── providers/llm/        # LLM provider adapters
│   ├── anthropic.py      # Claude
│   ├── ollama.py         # Ollama (local)
│   ├── openai_compat.py  # OpenAI + compatible APIs
│   └── registry.py       # Provider auto-detection
│
├── agents/               # Specialist AI agents
│   ├── financial/        # Fundamental, technical, macro, risk agents
│   └── realty/           # Real estate analysis agents
│
├── datasources/          # Market data source adapters
│   └── sources/          # yfinance, akshare, tushare
│
└── config/               # Configuration templates
```

---

## Development

```bash
# Clone and set up dev environment
git clone https://github.com/Cinsoul/aria-code.git
cd aria-code
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run tests
pytest tests/ -v

# Run the CLI
python3 aria_cli.py
```

See [CONTRIBUTING.md](./CONTRIBUTING.md) for guidelines.

---

## Relation to Arthera

Aria Code is the open-source CLI component of [Arthera](https://arthera.finance) — an AI-powered quantitative investment platform. The full Arthera platform includes a web dashboard, desktop terminal, iOS app, and institutional quant engine.

Aria Code is designed to work as a **standalone tool** — it doesn't require the Arthera backend. All financial calculations run locally. Cloud features (real-time A-share data, ML predictions) are optional and connect to your own self-hosted backend or the Arthera cloud service.

---

## License

MIT © 2025 Arthera Team — see [LICENSE](./LICENSE)

---

## Links

- 🌐 Website: [arthera.finance](https://arthera.finance)
- 📦 Full platform: [github.com/Cinsoul/Arthera](https://github.com/Cinsoul/Arthera)
- 🐛 Issues: [github.com/Cinsoul/aria-code/issues](https://github.com/Cinsoul/aria-code/issues)
- 💬 Discussions: [github.com/Cinsoul/aria-code/discussions](https://github.com/Cinsoul/aria-code/discussions)
