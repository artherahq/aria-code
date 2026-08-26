# Artifact 能力

> 差的从来不是渲染器，是「发布」得是一次工具调用。

## 之前缺的那一块

Artifact 的每个零件其实都已经有了：

| 零件 | 位置 | 状态 |
|------|------|------|
| 记录与版本 | `artifacts.py` | ✅ 按 category+topic 分组成 thread |
| 本地渲染 + SSE 实时更新 | `preview_server.py` | ✅ 含版本步进器 |
| 打开面板 | `/canvas` | ✅ |
| 跨进程发现（Electron 终端嵌入） | `canvas.json` 握手 | ✅ |
| **模型往上放东西的方法** | — | ❌ **缺** |

agent 只能写一个 HTML 文件，然后指望人类已经敲过 `/canvas` 并且想得起来去看。

这一块的缺失，就是「这个工具能产出文件」和「这个助手给你做了个能看的东西」之间的全部差别。
**值得抄的机制不是渲染器，而是「发布」是一次工具调用** —— 模型自己判断这个问题的正确答案
是视觉的，于是那个东西就出现了，带版本，就在对话旁边。

## `publish_artifact`

```
publish_artifact(
  content=..., filename="revenue.html",   # 或 path="report.html"
  title="Revenue Board",
  topic="Revenue Board",   # 版本 thread 的键，默认取 title
  description="..."
)
```

同一个 `topic` 再发布一次，就是给同一个 artifact 加一个版本，而不是新建一个 ——
所以「把那个看板改一下」的行为符合人的预期：迭代 = 重新发布。

工具描述里明确告诉模型页面必须自包含（内联 CSS/JS，图片用 `data:` URI），
因为渲染沙箱会拦掉所有网络请求。少了这句，模型会写
`<script src="https://cdn…">`，CSP 拦掉，artifact 渲染成一片空白。

### 它刻意不做的事

**它不会自己启动预览服务器。** `preview_server` 的设计就是 opt-in ——
在人类敲 `/canvas` 之前，这个模块里没有任何东西会运行 —— 而一个模型可以随意调用的工具，
恰恰是最不该用来挂一个自启动本地 HTTP 服务的东西。没有 canvas 在跑时，
发布照样记录并版本化，并告诉模型东西放在哪；人打开面板的那一刻就会看到。

## 安全：两层，缺一不可

Artifact 的 HTML 是模型生成的，并且会在浏览器里渲染。这有两个必须同时堵上的洞。

### 1. iframe 沙箱（修了一个真实 bug）

原本的代码是：

```js
sandbox="allow-scripts allow-same-origin"   // ← 等于没有沙箱
```

`allow-scripts` 和 `allow-same-origin` **不是叠加关系，它们互相抵消**。两个一起给，
被嵌入的文档就在宿主页面的 origin 里运行脚本 —— 它可以读 `/state`、连 `/events`、
读父页面 DOM。HTML 规范和 MDN 都明确警告过这个组合「允许被嵌入文档完全移除沙箱」。

现在是 `sandbox="allow-scripts"`：不透明 origin，脚本能跑，别的什么都够不着。

### 2. CSP（沙箱堵不住的那一半）

不透明 origin 并**不能**阻止页面往外发请求。所以每个 artifact 响应都带：

```
default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval';
style-src 'unsafe-inline' https://fonts.googleapis.com;
font-src https://fonts.gstatic.com data:; img-src data: blob:;
connect-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'self'
```

`connect-src 'none'` 是关键那行：没有它，artifact 里的 JS 可以把页面内容
（很可能就是 agent 刚刚分析出来的数据）POST 到互联网上任何一台主机。
Google Fonts 是唯一例外，因为一个静默丢掉字体的看板看起来像坏了而不像被保护了。

### 3. 工具侧：服务器看不到的那一半

服务器只知道自己在渲染一个文件，不知道这个文件是从哪来的。所以工具拒绝发布工作区之外的文件 ——
否则「把这个 artifact 发布出来」就成了把 `~/.ssh/id_rsa` 读进浏览器标签页的方法。
路径按 resolve 之后判断，指向外面的软链接和其他情况一起被挡掉。

## 顺手修的版本丢失

`create_user_artifact` 按秒命名文件。`publish_artifact` 是模型可以连着调两次的工具，
同一秒内的两次发布会解析到同一个路径 —— 后一次覆盖前一次，版本历史静默少一条。
现在会往后找一个没被占用的名字。

## 和 Claude Artifacts 还差什么

诚实地列一下，以及为什么：

| | 现状 | 说明 |
|---|---|---|
| 模型主动创建 | ✅ | `publish_artifact` |
| 版本历史、原地更新 | ✅ | topic thread |
| 沙箱隔离 | ✅ | opaque origin + CSP |
| 可分享的 URL | ❌ | 只绑 `127.0.0.1`。这是**刻意的**——把模型生成的页面挂上公网是一个产品决策，不是一个 bug |
| 页面持有运行时能力（读数据、存状态、回问模型） | ❌ | 需要一个受控的 host bridge；`connect-src 'none'` 下必须走 `postMessage` 而不是 fetch |
| 评论 / 协作 | ❌ | 需要服务端 |

前三项是「像 Claude 一样」的核心，已经具备。后三项都需要先决定要不要做托管服务 ——
一旦有了托管，`frame-ancestors` 和跨租户隔离就成了首要问题，而不是附带的。
