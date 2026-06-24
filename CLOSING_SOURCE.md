# 开源 → 闭源/商业化路线图 (Open-Core)

像 Claude Code 那样"源码不公开、二进制可用"的目标,不需要一次性闭源整个项目。
推荐 **open-core**:免费的 CLI 壳保持源码可见(已是 BSL 1.1),把**专有价值**编译后分发。
本仓库已为此铺好脚手架,下面是落地步骤。

## 已就位的脚手架

| 组件 | 作用 |
|---|---|
| `LICENSE` (BSL 1.1) | 源码可见但**禁止竞品/托管**;4 年后转 Apache 2.0 |
| `licensing.py` | 功能授权闸门 —— 免费功能默认开放,专业功能需 license(支持 HMAC 签名) |
| `packages/quant_engine/is_available()` | 可选导入边界 —— 引擎缺失时免费壳优雅降级 |
| `tools/build_quant_engine.py` | 用 Nuitka 把专有引擎编译成 `.so`(无源码) |
| `CLA.md` | 贡献者版权协议 —— 保留你单方 relicense 的权利 |

## 分阶段执行

### 阶段 0 — 现在(已完成)
- ✅ BSL 1.1 + PRIVACY + opt-in 同意。
- ✅ 专有数学已隔离在 `packages/quant_engine/`(期权定价、蒙特卡洛、Kelly、Dixon-Coles…)。
- ✅ 所有调用点走 `try/except` + `is_available()`,缺引擎不崩。

### 阶段 1 — 编译专有引擎
```bash
pip install nuitka
python tools/build_quant_engine.py --check    # 检查工具链
python tools/build_quant_engine.py --build     # 产出 dist_compiled/*.so
```
- 发布带 `.so`(而非 `.py`)的 wheel;反编译成本极高 ≈ 实质闭源。
- 免费壳把它作为**可选依赖** `import`,无授权时降级。

### 阶段 2 — 拆库
- 把 `packages/quant_engine` 移到**独立私有仓**(如 `arthera-quant`)。
- 私有仓 CI 跑阶段 1 的编译,发布到**私有 index**(或随付费账户分发)。
- 公开仓删除该目录;`is_available()` 自动返回 False,免费壳照常工作。

### 阶段 3 — 授权与付费
- 用 `licensing.py`:专业功能调 `require_feature("...")`。
- 签发 license:`~/.arthera/license.json` `{key,tier,features,exp,sig}`。
- 生产环境设 `ARIA_LICENSE_PUBKEY`(随构建分发的 HMAC 校验密钥)→ 强制签名校验,防伪造。

### 阶段 4 — 服务端化(最强护城河)
- 最高价值逻辑(实时因子、ML 训练、组合优化)逐步迁到**后端 API**。
- 服务端代码天生不可分发;客户端只拿结果。Claude Code 的核心能力也在服务端。

## 法律与社区
- **接受外部 PR 前先要求签 [CLA](CLA.md)** —— 否则贡献者保留版权,你将无法把含其代码的部分 relicense。
- ≤4.1.2 的 MIT 版本不可撤销;BSL 仅对之后版本生效。正式商业化前请律师复核 LICENSE/PRIVACY/CLA。

## 要避免的坑
- ❌ 把 license 校验当 IP 保护 —— 校验能被绕过;**真正的保护是编译**。
- ❌ 在免费壳里硬 `import` 专有引擎 —— 必须可选 + 降级(已用 `is_available()` 约束)。
- ❌ 无 CLA 就接受大量外部贡献 —— 会锁死你的 relicense 能力。
