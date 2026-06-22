<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg" width="100">
    <img src="docs/assets/logo-light.svg" alt="Aria Code" width="100">
  </picture>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/中文文档-当前-red?style=flat-square" alt="中文"/>
  <a href="./README.md"><img src="https://img.shields.io/badge/English-README.md-6366f1?style=flat-square" alt="English"/></a>
</p>

<p align="center">
  <a href="https://www.npmjs.com/package/@artheras/aria-code"><img src="https://img.shields.io/npm/v/@artheras/aria-code?style=for-the-badge&logo=npm&color=cb3837&label=npm" alt="npm"/></a>
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="python"/>
  <img src="https://img.shields.io/badge/Ollama-本地大模型-black?style=for-the-badge&logo=llama&logoColor=white" alt="ollama"/>
  <img src="https://img.shields.io/badge/云端-19+供应商-f59e0b?style=for-the-badge" alt="providers"/>
  <img src="https://img.shields.io/badge/协议-MIT-22c55e?style=for-the-badge" alt="license"/>
  <img src="https://img.shields.io/github/stars/artherahq/aria-code?style=for-the-badge&color=f59e0b" alt="stars"/>
</p>

<h1 align="center">Aria Code</h1>

<p align="center">
  <b>命令行驱动的 AI 金融终端</b><br>
  <sub>完全离线可用 · 19+ 云端供应商 · 系统语言自动识别 · 专为投资者和量化研究者设计</sub>
</p>

<p align="center">
  <a href="#-快速开始">快速开始</a> ·
  <a href="#-键盘快捷键">快捷键</a> ·
  <a href="#-模型支持">模型</a> ·
  <a href="#-命令参考">命令</a> ·
  <a href="#-飞书集成">飞书</a> ·
  <a href="#-telegram-集成">Telegram</a> ·
  <a href="#-架构">架构</a>
</p>

<p align="center">
  <img src="docs/assets/demo-2026-06-22.gif" alt="Aria Code demo" width="860"/>
</p>

---

## 什么是 Aria Code？

Aria Code 是一款**终端优先的 AI 金融智能体** — 想象成 Claude Code，但内置了深度金融领域知识，且支持完全离线运行。你可以在终端里直接问它股票、投资组合优化、量化策略或代码问题，它会用真实数据、公式和分析实时回答。

```
$ aria-code

  ▣ Aria Code  v4.1  本地优先智能体
  模型      qwen2.5-coder:7b  本地
  工作区    ~/my-portfolio
  模式      工作区写入 · 联网开启 · 仅本地
  状态      Ollama 在线 · 3 个模型

  试试  分析 AAPL  ·  /project load ./myapp  ·  /help

> 分析贵州茅台的动量 — 给我 RSI、MACD 和简短的投资论点

  贵州茅台 (600519)  ── 技术快照
  ──────────────────────────────────────────────
  现价     ¥1,680.00    今日 +1.8%          (实时行情)
  RSI(14)  62.3         中性偏强，未超买
  MACD     +3.1         3天前金叉，趋势向上
  布林带   0.14         波动率较低，稳定上涨

  信号：↑ 看多  (动量完好，关注 RSI 是否突破 70)
  支撑：¥1,620 / ¥1,580     压力：¥1,720 / ¥1,800

  论点：白酒消费复苏 + 茅台直销渠道扩大，机构资金持续流入…

  2.1s · qwen2.5-coder:7b (本地)
```

---

## ✨ v4.1 新特性

| 功能 | 说明 |
|------|------|
| **Bloomberg UI** | `/ui <描述>` 生成 Bloomberg 终端风格 HTML 看板 — 黑底琥珀色、IBM Plex Mono 数字、零圆角、自动深浅色 |
| **工具透明度** | 每个工具调用完成后显示 `✓ 动作 (42ms)` · 每 turn 显示费用 · 多步命令加阶段分隔线 |
| **用户档案** | `~/.arthera/ARIA.md` 每次会话自动注入 · `/memory profile add <内容>` 持久化个人偏好 |
| **量化引擎** | Citadel/Jane Street 风格 5 模块引擎 · 涨停预测 · 动态股票池 |
| **MCP 工具** | 新增 5 个量化 MCP 工具 |
| **83 个命令** | 从 ~150 收敛到 83 个直接动作命令；其余交给 LLM 自然语言处理 |
| **LLM 路由修复** | 模型现在知道可以调取实时数据工具，不再说"我没有实时数据" |

完整历史见 [CHANGELOG.md](CHANGELOG.md)。

### v4.0 主要特性

| 功能 | 说明 |
|------|------|
| ⌨️ **键盘快捷键** | `Shift+Tab` 切换权限 · `Alt+T` 思考模式 · `Alt+P` 模型切换 · `Ctrl+O` 对话记录 · `Ctrl+T` 任务列表 |
| `!` **Shell 模式** | 输入 `! git status` 直接执行系统命令，输出自动加入 AI 上下文 |
| `@` **文件自动补全** | 在任意位置输入 `@src/` 即可补全文件路径 |
| 🌍 **系统语言自动识别** | 首次运行自动读取 OS 语言，界面和提示语中英文随系统切换 |
| 🤖 **19+ 云端供应商** | Google Gemini · xAI Grok · Mistral · Cohere · Perplexity · 百度文心 · 豆包 · MiniMax · 阶跃星辰 · 零一万物 + 全部原有供应商 |
| 🔢 **全系 Ollama 模型** | Qwen3 · DeepSeek-R1 · Llama 3.x · Phi-4 · Gemma3 · Mistral 全家桶 |

---

## ✨ 核心功能

| 功能 | 详情 |
|------|------|
| 🦙 **100% 离线模式** | 基于 Ollama — 无需 API Key，数据不离本机 |
| 📊 **金融智能** | DCF / WACC / PE / 夏普 / Kelly / Black-Scholes 等 30+ 内置公式 |
| 📈 **实时行情** | A 股（东财）· 美股（Finnhub）· 港股 · 加密货币（ccxt） |
| 🔍 **量化研究** | `/backtest` `/signal` `/kelly` `/factor` `/portfolio` `/screen` `/corr` `/ptbt` |
| 🤖 **19+ 云端供应商** | 国际主流 + 国内主流 LLM API 全覆盖 |
| 🔌 **MCP 协议** | 对接任意 [Model Context Protocol](https://modelcontextprotocol.io) 服务器 |
| ⌨️ **丰富键盘体验** | Vim 模式 · `!` Shell · `@` 文件 · `Shift+Tab` 权限 · 对话记录查看 |
| 💬 **飞书 / Telegram** | 从任意聊天 App 随时问 Aria |
| 📱 **iOS 推送提醒** | 通过 APNs 实时推送价格告警 |
| 🌍 **自动双语** | 首次运行自动检测 OS 语言；AI 回复语言跟随用户输入 |
| 🏠 **房产分析** | 物业估值、REITs 筛选、租金回报率、全国 70 城房价 |

---

## 🚀 快速开始

### 方式一：Bootstrap 一键安装（新 Mac / Linux 推荐）

全新电脑无需任何前置条件，一条命令搞定：

```bash
curl -fsSL https://raw.githubusercontent.com/artherahq/aria-code/aria-code/bootstrap.sh | bash
```

自动完成以下步骤：
- ✅ 安装 **Xcode 命令行工具**（macOS）— 提供 git、make、编译器
- ✅ 安装 **Homebrew**（macOS 包管理器）
- ✅ 安装 **Python 3.12**（如未安装）
- ✅ 克隆仓库到 `~/aria-code`
- ✅ 运行 `install.sh` 创建虚拟环境、安装依赖、注册 `aria-code` 命令

> 已经克隆了仓库？在文件夹内直接运行 `bash bootstrap.sh` 即可。

### 方式二：npm 安装（需 Node.js ≥ 16）

已安装 [Node.js](https://nodejs.org) 的用户，npm 安装器会自动处理 Python、Xcode CLT 和 Homebrew：

```bash
npm install -g @artheras/aria-code
aria-code
```

自动完成的步骤：
- ✅ 检测 / 安装 Xcode 命令行工具（macOS）
- ✅ 检测 / 安装 Homebrew（macOS）
- ✅ 未找到 Python 时自动安装 Python 3.12
- ✅ 克隆 Aria Code 到 `~/.aria-code/`
- ✅ 创建 venv 并安装所有 Python 依赖

更新：`npm update -g @artheras/aria-code`

修复：`npm explore -g @artheras/aria-code -- npm run repair`

### 方式三：Git Clone（已安装 Python 3.10+）

```bash
git clone https://github.com/artherahq/aria-code.git
cd Aria-Code
bash install.sh
```

添加到 PATH（如有提示）：

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc
```

### 方式三：Windows

```powershell
git clone https://github.com/artherahq/aria-code.git
cd Aria-Code
.\install.ps1
```

### 方式四：直接运行（无需安装）

```bash
git clone https://github.com/artherahq/aria-code.git
cd Aria-Code
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 aria_cli.py
```

### 第一步：安装 Ollama（本地大模型 — 免费，完全离线）

```bash
# macOS / Linux
curl -fsSL https://ollama.ai/install.sh | sh

# 拉取模型（选一个 — 首次运行自动发现已安装模型）
ollama pull qwen2.5-coder:7b    # 推荐 — 速度快，中文支持优秀（~4.7GB）
ollama pull qwen3:8b            # 最新千问，推理能力更强
ollama pull deepseek-r1:7b      # 强推理，适合复杂量化任务
ollama pull llama3.2:3b         # 最小最快（~2GB）
ollama pull phi4-mini           # 微软 Phi-4 mini，代码出色
```

首次运行 Aria 时会自动发现并选择最佳已安装模型，无需任何配置。

### 第二步：配置云端 API Key（均可选）

```bash
# 交互式配置向导（支持全部 19 家供应商，中英双语）
python3 setup_wizard.py

# 或手动复制并编辑
cp .env.example .env
```

向导现已支持全部 19 家云端供应商，包括 Google Gemini、xAI Grok、Mistral、百度文心、豆包、MiniMax 等。

---

## ⌨️ 键盘快捷键

Aria Code 基于 `prompt_toolkit` 构建了完整的键盘快捷键系统：

### 通用快捷键

| 快捷键 | 功能 |
|--------|------|
| `Shift+Tab` | 循环切换权限模式：`只读` → `工作区写入` → `完全访问` |
| `Alt+T` | 开/关思考模式（扩展推理） |
| `Alt+P` | 打开模型切换器（自动填入 `/model`） |
| `Ctrl+O` | 切换对话记录查看器 — 显示所有工具调用和时间戳 |
| `Ctrl+T` | 切换任务列表 — 实时显示待办/进行中/已完成 |
| `Ctrl+L` | 重绘终端界面（修复显示错乱） |
| `Ctrl+C` | 取消当前响应 / 清空输入 |
| `Ctrl+D` | 退出 Aria |
| `Esc` | 中断流式响应 |

### 输入模式

| 前缀 | 模式 | 示例 |
|------|------|------|
| `/` | 斜杠命令（模糊自动补全） | `/backtest momentum SPY` |
| `!` | Shell 模式 — 运行命令，输出加入上下文 | `! git diff HEAD~1` |
| `@` | 文件路径自动补全 | `@src/components/` |
| `"""` | 多行输入模式（以 `"""` 结束） | 粘贴大段代码 |

### 底部工具栏（始终显示）

```
qwen2.5-coder:7b · ~/my-project ⎇ main ✓3/5 · rw · 仅本地 · /help · 1,240/16,384
│                    │           │      │       │    │
│                    │           │      │       │    └── 上下文用量
│                    │           │      │       └── 隐私状态
│                    │           │      └── 权限：ro/rw/full（颜色区分）
│                    │           └── 任务进度
│                    └── git 分支
└── 当前模型
```

---

## 🤖 模型支持

### 本地模型（通过 Ollama — 离线，免费）

| 模型 | 命令 | 大小 | 适用场景 |
|------|------|------|----------|
| **qwen2.5-coder:7b** ⭐ | `ollama pull qwen2.5-coder:7b` | 4.7GB | 代码 + 中文（推荐） |
| qwen3:8b | `ollama pull qwen3:8b` | 5.2GB | 最新千问，推理更强 |
| qwen3:30b-a3b | `ollama pull qwen3:30b-a3b` | 17GB | 高性能版本 |
| deepseek-r1:7b | `ollama pull deepseek-r1:7b` | 4.7GB | 数学/推理强 |
| deepseek-r1:1.5b | `ollama pull deepseek-r1:1.5b` | 1.1GB | 超轻量推理 |
| llama3.2:3b | `ollama pull llama3.2:3b` | 2GB | 通用，最快 |
| llama3.1:8b | `ollama pull llama3.1:8b` | 4.7GB | 通用目的 |
| mistral:7b | `ollama pull mistral:7b` | 4.1GB | 欧洲品质 |
| phi4-mini | `ollama pull phi4-mini` | 2.5GB | 代码出色，体积小 |
| gemma3:4b | `ollama pull gemma3:4b` | 3.3GB | Google，高效 |

随时切换模型：

```bash
/model                    # 交互式选择（显示安装状态）
/model qwen3:8b           # 直接切换
/model openai/gpt-4.5     # 切换到云端模型
Alt+P                     # 键盘快捷键
```

### 云端供应商（19+ 家支持）

#### 国际供应商

| 供应商 | 模型 | 环境变量 |
|--------|------|----------|
| **Anthropic** | Claude Sonnet 4、Opus 4 | `ANTHROPIC_API_KEY` |
| **OpenAI** | GPT-4.5、o3、o4-mini | `OPENAI_API_KEY` |
| **DeepSeek** | deepseek-chat、deepseek-reasoner | `DEEPSEEK_API_KEY` |
| **Google Gemini** | gemini-2.0-flash、2.5-pro | `GOOGLE_API_KEY` |
| **xAI Grok** | grok-3、grok-3-fast | `XAI_API_KEY` |
| **Groq** | llama-3.3-70b（高速推理） | `GROQ_API_KEY` |
| **Mistral** | mistral-large、codestral | `MISTRAL_API_KEY` |
| **Cohere** | command-r-plus | `COHERE_API_KEY` |
| **Perplexity** | sonar-pro（含网络搜索） | `PERPLEXITY_API_KEY` |
| **Together AI** | 100+ 开源模型 | `TOGETHER_API_KEY` |

#### 国内供应商

| 供应商 | 模型 | 环境变量 |
|--------|------|----------|
| **硅基流动 SiliconFlow** | Qwen/DeepSeek 托管版 | `SILICONFLOW_API_KEY` |
| **阿里百炼 DashScope** | qwen-max、qwen-turbo | `DASHSCOPE_API_KEY` |
| **月之暗面 Kimi** | moonshot-v1-128k | `MOONSHOT_API_KEY` |
| **智谱 GLM** | glm-4-plus | `ZHIPU_API_KEY` |
| **百度千帆 ERNIE** | ernie-4.5-turbo | `QIANFAN_ACCESS_KEY` |
| **字节跳动 豆包** | （基于 endpoint-id） | `ARK_API_KEY` |
| **MiniMax** | MiniMax-Text-01 | `MINIMAX_API_KEY` |
| **阶跃星辰 StepFun** | step-2-16k | `STEPFUN_API_KEY` |
| **零一万物 Yi** | yi-large | `ONEAI_API_KEY` |

使用任意供应商：

```bash
/model anthropic/claude-sonnet-4-6
/model google/gemini-2.0-flash-exp
/model baidu/ernie-4.5-turbo-128k
/model moonshot/moonshot-v1-128k
/apikey       # 19 家供应商交互式配置向导
```

---

## ⚡ 命令参考

### 行情与市场

```bash
/quote AAPL MSFT TSLA              # 美股实时多标的行情（Finnhub）
/quote 000001 600519 300750        # A股行情（东方财富）
/quote BTC/USDT ETH/USDT           # 加密货币价格
/news AAPL                         # 最新财经新闻
/regime                            # 市场情绪（牛/熊/中性）
/alert add AAPL gt 200             # 设置价格告警
/alert list                        # 查看所有告警
```

### 量化研究

```bash
/signal TSLA                       # 技术信号（RSI / MACD / 布林带）
/backtest momentum SPY 2023-01-01 2024-12-31
/backtest ml 600519 300750 NVDA    # ML 信号回测（3 策略对比）
/wf SPY momentum                   # 滚动窗口回测
/kelly AAPL 0.6 2.0                # Kelly 公式 — 仓位建议
/factor PE PB ROE                  # 多因子分析
/screen PE<15 ROE>20               # 因子筛选器
/portfolio AAPL MSFT GOOGL         # 投资组合优化
/ptbt AAPL MSFT GOOG 0.4 0.3 0.3  # 组合回测（指定权重）
/corr AAPL MSFT TSLA SPY           # 相关性矩阵
/ichimoku AAPL                     # 一目均衡表
/options AAPL calls 2025-01        # 期权链
/quality AAPL                      # Piotroski + Altman Z 值
```

### 分析

```bash
/analyze AAPL                      # AI 综合分析
/peer AAPL MSFT GOOGL META         # 竞争对手对比
/macro                             # 宏观面板（GDP / CPI / 利率）
/macro cn                          # 中国宏观数据
/sector tech                       # 行业分析
/realty Shanghai Pudong            # 房地产分析
/feargreed                         # 加密恐贪指数
/funding BTC ETH                   # 永续合约资金费率
```

### 会话与界面

```bash
/btw 那个函数叫什么来着？          # 旁白提问 — 不污染对话历史
/recap                             # 会话摘要（轮次 + 话题）
/clear                             # 清空对话
/compact                           # 智能上下文压缩
/history                           # 查看最近对话
/sessions                          # 列出已保存会话
/export md report.md               # 导出对话
/rename "NVDA 研究"                # 给当前会话命名
```

### 系统

```bash
/model                             # 查看/切换模型（交互式）
/apikey                            # 19 家供应商 API Key 配置向导
/config set ui_lang=zh             # 强制中文界面
/config set ui_lang=en             # 强制英文界面
/thinking on                       # 开启扩展思考模式
/privacy status                    # 隐私设置
/tools                             # 列出所有启用工具
/skills                            # 列出技能
/mcp list                          # MCP 服务器状态
/doctor                            # 诊断安装问题
/providers                         # 所有供应商状态
```

---

## 🌍 语言自动识别

首次运行时，Aria 自动读取系统 locale 并设置界面语言：

```bash
# 中文系统 → 中文界面 + 中文提示
LANG=zh_CN.UTF-8  →  本地优先智能体 · Ollama 在线 · 试试 分析 AAPL

# 英文系统 → 英文界面 + 英文提示
LANG=en_US.UTF-8  →  local-first agent · Ollama online · try analyze AAPL
```

AI **输出语言**始终跟随你的输入 — 用中文问，得中文答；用英文问，得英文答。

随时手动切换：

```bash
/config set ui_lang=zh    # 强制中文
/config set ui_lang=en    # 强制英文
/config set ui_lang=auto  # 恢复系统自动检测
```

---

## 💬 飞书集成

将 Aria 接入飞书（Lark），在任意群聊或私信中随时提问。

### 工作原理

```
你的飞书消息
       │
       ▼
  飞书服务器
       │
  ┌────┴─────────────────────────────────────┐
  │  模式 A：中继（推荐，5 分钟）             │  模式 B：自建应用（20 分钟）
  │  Aria 中继服务器                          │  飞书开放平台自建应用
  │  wss://relay.aria.ai                      │  需要公网 IP 或内网穿透
  └────┬─────────────────────────────────────┘
       │
       ▼
 aria_relay_client.py（你的电脑）
       │
       ▼
 aria_cli.py → LLM → 回复发送回去
```

### 模式 A：中继（推荐）

```bash
python3 setup_wizard.py
# 选择「飞书中继模式」
# 输出：✅ 你的 Client ID：ARIA-xxxxxxxx-xxxx
```

在飞书中发送消息给 **Aria Bot**：

```
/bind ARIA-xxxxxxxx-xxxx
```

配置 `~/.aria/.env`：

```env
ARIA_RELAY_URL=wss://relay.aria.ai
ARIA_RELAY_CLIENT_ID=ARIA-xxxxxxxx-xxxx
ARIA_RELAY_MODE=relay
ARIA_CODE_DIR=~/aria-code
```

启动：

```bash
python3 aria_daemon.py start
```

### 模式 B：自建飞书应用

1. 打开[飞书开放平台](https://open.feishu.cn/app) → 创建自定义应用
2. 设置事件 URL：`https://yourdomain.com/api/v1/feishu/webhook`
3. 订阅 `im.message.receive_v1`

```env
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ARIA_RELAY_MODE=own_app
```

---

## 📱 Telegram 集成

### 配置

1. 给 **@BotFather** 发送 `/newbot` → 复制 **Bot Token**
2. 给 **@userinfobot** 发送消息 → 复制 **Chat ID**

配置环境变量：

```env
TELEGRAM_BOT_TOKEN=1234567890:ABCDEFGxxxxxxxxxxxxxx
TELEGRAM_ALLOWED_IDS=123456789
ARIA_CODE_DIR=~/aria-code
```

启动：

```bash
python3 aria_daemon.py start
```

在 Telegram 中使用：

```
/price AAPL                → 苹果实时行情
/price 600519              → 茅台 A 股
/price BTC/USDT            → 比特币
分析 NVDA 的动量            → AI 综合分析
```

---

## 🏗️ 架构

```
┌──────────────────────────────────────────────────────────────────┐
│                         Aria Code v4.0                           │
│                                                                  │
│  ┌──────────┐  ┌────────────┐  ┌──────────┐  ┌─────────────┐  │
│  │ 终端 CLI │  │ 飞书机器人 │  │Telegram  │  │  Webhook    │  │
│  │          │  │（中继/自建）│  │  机器人  │  │  外部接口   │  │
│  └────┬─────┘  └─────┬──────┘  └────┬─────┘  └──────┬──────┘  │
│       └───────────────┴──────────────┴────────────────┘         │
│                               │                                  │
│                     ┌─────────▼──────────┐                      │
│                     │   aria_daemon.py   │                      │
│                     │     消息路由器      │                      │
│                     └─────────┬──────────┘                      │
│                               │                                  │
│              ┌────────────────┼────────────────┐                │
│              │                │                │                │
│   ┌──────────▼───┐  ┌─────────▼───┐  ┌────────▼──────┐        │
│   │  LLM 路由器  │  │  工具执行   │  │   数据层      │        │
│   │19+ 供应商    │  │  bash/文件  │  │Finnhub/东财   │        │
│   └──────────────┘  └─────────────┘  └───────────────┘        │
└──────────────────────────────────────────────────────────────────┘
```

### 文件结构

```
aria-code/
├── aria_cli.py               # 主 CLI + REPL（键盘快捷键、! Shell、@文件）
├── aria_daemon.py            # 后台守护进程 + 定时任务
├── market_data_client.py     # 统一行情数据层（美股优先走 Finnhub）
├── setup_wizard.py           # 双语配置向导（19 家供应商）
│
├── apps/cli/
│   ├── i18n.py               # 语言自动检测 + 界面字符串翻译
│   ├── commands/
│   │   └── model_cmds.py     # /model /apikey /providers（19 家云端供应商）
│   ├── prompts/
│   │   └── coding.py         # 代码生成提示（end_date 修复、akshare 降级）
│   └── tools/
│       └── market_tools.py   # 行情工具（Finnhub dp 字段）
│
├── ui/
│   ├── banner.py             # 双语横幅（i18n 感知）
│   └── completer.py          # 模糊自动补全：/ 命令 · @ 文件 · ! 历史
│
├── providers/llm/            # LLM 适配器（19+ 云端 endpoint）
├── agents/financial/         # 基本面 / 技术面 / 宏观 / 风险 / 综合智能体
├── brokers/                  # 国内（富途/长桥/老虎）+ 国际（IBKR/Alpaca）
└── datasources/sources/      # yfinance · akshare · FRED · EDGAR · Finnhub
```

---

## 📡 市场数据来源

| 数据源 | 覆盖范围 | API Key |
|--------|----------|---------|
| **Finnhub** ⭐ | 美股实时行情（主要）+ 财报 | 可选（免费档位） |
| **东方财富** | A 股实时、北向资金、涨跌停 | 无（免费） |
| **akshare** | A 股历史、财务、行业数据 | 无（免费） |
| **yfinance** | 美/港/全球股票、ETF、外汇、历史 | 无（免费） |
| **ccxt** | 100+ 加密交易所 | 无（免费档位） |
| **FRED** | 美联储宏观数据 — GDP、CPI、利率 | 可选（免费注册） |
| **SEC EDGAR** | 美股 10-K / 10-Q 报告 | 无（免费） |
| Alpha Vantage | 美股历史 + 基本面 | 可选（免费档位） |
| Polygon | 美股专业数据 | 可选（免费档位） |
| Tushare | A 股完整数据 | 可选（免费 Token） |

---

## 🔌 MCP 集成

对接任意 [Model Context Protocol](https://modelcontextprotocol.io) 服务器：

```json
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

```bash
/mcp list      # 列出已连接的 MCP 服务器
/mcp status    # 服务器健康状态
/mcp tools     # 所有可用 MCP 工具
```

---

## ⚙️ 配置

设置存储在 `~/.arthera/config.json`。在项目目录添加 `.ariarc` 可覆盖项目级配置：

```json
{
  "model": "qwen2.5-coder:7b",
  "ui_lang": "auto",
  "market": "cn",
  "permission_mode": "workspace-write",
  "default_symbols": ["000001", "600519", "300750", "NVDA"],
  "thinking": false
}
```

### LLM 供应商优先级

Aria 自动选择第一个可用供应商：

```
本地 Ollama  →  Anthropic  →  OpenAI  →  DeepSeek  →  Google  →  xAI  →  Groq  →  …
（离线优先）    （推理强）    （通用）    （性价比）    （多模态）  （联网）  （高速）
```

强制本地模式：`ARIA_MODEL=ollama/qwen2.5-coder:7b`

---

## 🛠️ 环境要求

- Python **3.10+**
- [Ollama](https://ollama.ai)（强烈推荐，用于离线模式）
- 内存：4GB+（7B 模型建议 8GB+）
- macOS · Linux · Windows（WSL2）

```bash
pip install -r requirements.txt
```

核心依赖：`rich` · `prompt_toolkit` · `yfinance` · `akshare` · `ccxt` · `pandas` · `numpy`

---

## 🤝 参与贡献

欢迎贡献！请查看 [CONTRIBUTING.md](./CONTRIBUTING.md)。

```bash
git clone https://github.com/artherahq/aria-code.git
cd aria-code
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest tests/ -v
```

---

## 与 Arthera 的关系

Aria Code 是 [Arthera](https://arthera.finance) 的开源命令行组件 — Arthera 是一款 AI 驱动的量化投资平台，完整版包括 Web 仪表盘、桌面终端、iOS App 和机构级量化引擎。

Aria Code 设计为**独立工具** — 无需 Arthera 后端即可运行。所有金融计算在本地完成。云端功能均可选。

---

## 许可证

MIT © 2025 Arthera Team — 详见 [LICENSE](./LICENSE)

---

<p align="center">
  <a href="https://arthera.finance">官网</a> ·
  <a href="https://github.com/Cinsoul/Arthera">完整平台</a> ·
  <a href="https://github.com/artherahq/aria-code/issues">问题反馈</a> ·
  <a href="https://github.com/artherahq/aria-code/discussions">讨论社区</a>
</p>
