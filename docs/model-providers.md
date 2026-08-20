# 接入自己的模型（本地 / 自建 / 任意云端）

Aria Code 内置了 12 个 provider（ollama、lmstudio、deepseek、openai、anthropic、
groq、siliconflow、dashscope、moonshot、zhipu、together、local）。要接入**未列出**
的服务——自建 vLLM、公司内网网关、Arthera 自己的云端 API、任何 OpenAI 兼容
端点——不需要写 Python，在配置文件里声明即可。

## 三个原则

1. **配置里只写环境变量名，不写密钥值。** 配置文件常被备份、同步、误提交，
   明文密钥的暴露面比环境变量大一个量级。检测到 `api_key` / `token` 这类明文
   字段时，该 provider 会被**拒绝加载**并打印替代写法——不是静默忽略，因为
   静默忽略会让你以为配好了，实际请求全部 401。
2. **一个坏条目不连累其他。** 某个 provider 写错只会跳过它自己，其余照常可用。
3. **凭证与策略分开。** 密钥进环境变量（`~/.aria/.env`），选哪个模型进
   `providers.yaml`。

## 配置位置

按以下顺序查找第一个存在的文件：

```
~/.arthera/providers.json      # CLI 的 /apikey 命令写这里
~/.aria/providers.yaml         # 推荐手写这个
~/.aria/providers.json
./.aria.json  ./.aria.yaml     # 项目级覆盖
```

## 完整示例

`~/.aria/providers.yaml`：

```yaml
llm:
  # 默认模型
  default: mycorp/qwen-72b

  # 后备链：默认不可用时按顺序尝试
  fallback:
    - ollama/qwen2.5:7b
    - arthera-cloud/gemini-1.5-pro
    - deepseek/deepseek-chat

  # 按任务类型选模型
  code_tasks:     ollama/qwen2.5-coder:7b
  heavy_analysis: arthera-cloud/gemini-1.5-pro
  fast_response:  groq/llama-3.3-70b-versatile

# 声明内置列表之外的 provider
model_providers:
  mycorp:
    name: 公司内网推理网关          # 显示用
    base_url: https://llm.internal.example.com/v1
    env_key: MYCORP_LLM_TOKEN       # ← 只写变量名
    env_key_instructions: 找基础架构组申请，写进 ~/.aria/.env
    model: qwen-72b                 # 该 provider 的默认模型
    timeout: 120
    http_headers:                   # 固定请求头
      X-Tenant: research
    env_http_headers:               # 值来自环境变量的头；变量未设时**不发**该头
      X-Trace-Id: MYCORP_TRACE_ID

  arthera-cloud:
    name: Arthera 云端（Cloud Run）
    base_url: https://your-api.run.app/api/v2
    env_key: ARTHERA_API_TOKEN
```

对应的 `~/.aria/.env`：

```bash
MYCORP_LLM_TOKEN=实际的token
ARTHERA_API_TOKEN=实际的token
```

声明之后，`mycorp/<任意模型>` 就能像内置 provider 一样用在
`default` / `fallback` / `code_tasks` 里。

## 常见场景

**本地模型（已内置，无需声明）**

```yaml
llm:
  default: ollama/qwen2.5:7b        # Ollama，默认 http://localhost:11434
  fallback: [lmstudio/local-model]  # LM Studio，默认 http://localhost:1234
```

改端口用环境变量 `OLLAMA_BASE_URL` / `LMSTUDIO_BASE_URL`。

**自建 vLLM / OpenAI 兼容服务**

```yaml
model_providers:
  vllm:
    base_url: http://192.168.1.50:8000/v1
    model: Qwen2.5-72B-Instruct
    # 无鉴权就不写 env_key
```

**覆盖内置 provider**（比如把 openai 指向自建网关）

同名声明会覆盖内置项——用户显式写下的配置优先。

## 排查

```
/config          # 列出所有 provider 及可用状态，自定义项标 source=custom
```

`available=False` 时会给出具体缺哪个环境变量，以及你在
`env_key_instructions` 里写的获取说明。

## 与 codex 的对应关系

本设计对标 codex 的 `model_providers`（`~/.codex/config.toml`）。字段语义一致：
`base_url` / `env_key` / `env_key_instructions` / `http_headers` /
`env_http_headers`。差别是 codex 还提供明文 `experimental_bearer_token`，
但其源码注释写着 *"Use of this config is discouraged in favor of `env_key` for
security reasons"*——所以这里不提供该入口。
