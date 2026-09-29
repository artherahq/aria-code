<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/aria-code-icon.png">
    <img src="docs/assets/aria-code-icon-light.png" alt="Aria Code 图标" width="88">
  </picture>
</p>

<h1 align="center">Aria Code</h1>

<p align="center">面向代码与研究的终端 AI 工作台。<br>
在项目中处理文件，选择本地或云端模型，并按需连接专业领域工具。</p>

<p align="center">简体中文 · <a href="README.md">English</a></p>

<p align="center">
  <a href="https://www.npmjs.com/package/@artheras/aria-code"><img src="https://img.shields.io/npm/v/@artheras/aria-code?style=flat-square&logo=npm&label=npm" alt="npm 版本"></a>
  <a href="https://pypi.org/project/aria-code/"><img src="https://img.shields.io/pypi/v/aria-code?style=flat-square&logo=pypi&label=PyPI" alt="PyPI 版本"></a>
  <a href="https://github.com/artherahq/aria-code/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/artherahq/aria-code/ci.yml?branch=main&style=flat-square&logo=githubactions&label=CI" alt="CI 状态"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-BSL%201.1-64748b?style=flat-square" alt="Business Source License 1.1"></a>
</p>

<p align="center"><img src="docs/assets/demo-coding-workflow.gif" alt="Aria Code 真实终端录制：测试失败、读取代码、修改代码、测试通过" width="860"></p>
<p align="center"><sub>录制来自 Aria Code 在临时测试项目中的真实终端输出，仅缩短了等待时间。演示使用 CLI 命令直接操作，不代表模型自主修改。<a href="docs/assets/demo-coding-workflow.png">查看静态截图</a> · <a href="scripts/render_terminal_capture.py">查看录制渲染脚本</a>。</sub></p>

## Aria Code 能做什么

Aria Code 可以检查项目文件、提出并应用修改、运行命令和测试，以及解释改动。它还提供研究与可选的领域工具，包括金融分析和只读仓储运营分析。这些工具需要各自的数据源或集成；新安装的 CLI 不会自动获得实时业务数据。

CLI 与 Aria 的其他入口使用同一套核心运行时。模型选择与工具权限是两回事：你可以使用本地 Ollama 模型、配置云端供应商，或登录 Arthera 服务。云端模型和实时数据源都需要联网。

## 快速开始

需要 Python 3.10 或更新版本。从 PyPI 安装 CLI 并进入交互会话：

```bash
python3 -m pip install --upgrade aria-code
aria-code
```

如果更习惯 npm 启动器，可以使用 `npm install -g @artheras/aria-code`。源码开发方式见 [CONTRIBUTING.md](CONTRIBUTING.md)。

使用本地模型时，先安装 [Ollama](https://ollama.com/download)，拉取一个编码模型，再以仅本地模式启动：

```bash
ollama pull qwen2.5-coder:7b
aria-code --local
```

也可以在现有项目中执行单次任务：

```bash
aria-code -p "检查这个项目，修复失败的测试，运行测试并总结代码差异。"
```

Aria 会根据所配置的权限模式，在编辑文件或运行命令前请求批准。提交代码前请检查改动和测试结果。

## 模型与账户

| 使用方式 | 需要准备 |
| --- | --- |
| 本地 Ollama | 已安装并运行的 Ollama 模型；本地推理无需云端账户。 |
| 直接连接云端供应商 | 对应供应商的 API 凭证和网络连接。 |
| 直接连接 Google Cloud Vertex AI | `aria-code[google]`、Google Cloud 应用默认凭证、`GOOGLE_CLOUD_PROJECT` 和 `GOOGLE_CLOUD_LOCATION`。 |
| Arthera 托管服务 | 安装 `aria-code[google]`，然后在 CLI 中使用 `/login` 登录。Arthera 账户登录不会自动给本机授予 Vertex AI 凭证。 |

只安装所需功能的可选依赖；当前安装选项以 [pyproject.toml](pyproject.toml) 为准。本地推理可以离线运行，但实时行情、远程集成、登录和云端模型不能离线使用。

## 继续了解

| 主题 | 文档 |
| --- | --- |
| 架构与运行时 | [架构](docs/architecture.md) |
| 工具权限与数据边界 | [安全说明](docs/aria-code-safety.md) |
| 仓储智能体及只读数据契约 | [仓储 ERP 智能体](docs/warehouse-erp-agents.md) |
| 策略与回测流程 | [策略工作台](docs/strategy_workspace.md) |
| 更多示例 | [示例](examples/README.md) |
| 版本变化 | [更新日志](CHANGELOG.md) |

当前命令行参数请运行 `aria-code --help`；交互会话内可运行 `/help`。这比在 README 中复制一份容易过时的完整命令清单更可靠。

## 参与贡献与许可

欢迎参与贡献，见 [CONTRIBUTING.md](CONTRIBUTING.md)。Aria Code 使用 [Business Source License 1.1](LICENSE)；使用或再分发前请查看许可证中的具体参数。
