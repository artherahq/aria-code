# 行业 Pack

> 加一个行业，不是给路由加一个分支。

## 契约

`packs/base.py` 定义的规则只有一条，但它决定了一切：

> **只有当 pack 从用户这条消息里解析出一个具体实体时，它才激活。**

行业词汇本身永远不够。「分析」是普通中文，「AAPL」才是标的；「物流成本」是词汇，
`SF1234567890123` 才是运单。上一条助手回复说过什么，也不能激活任何东西。

这条规则是 pack 可组合的原因：未激活的 pack 不贡献 handler、不贡献工具、不贡献提示词，
所以 N 个行业的维护成本是 O(N)，请求时成本是 O(1)，而不是一个路由里的 N×M 个分支。

这条规则针对的是真实事故：一次关于本仓库的提问被回答成了 MongoDB 的股价
（前一条回复里的 "MongoDB" 被解析成了代码 MDB），一条含「行情」的消息让 REPL
阻塞了几十秒去下载完整的 A 股和港股标的表。物流或医疗用户会付一模一样的税。

## 内置的四个

| Pack | 实体 | 怎么算「解析出来」而不是「看着像」 |
|------|------|-----------------------------------|
| `finance` | 股票/ETF 代码 | `$AAPL`、A 股六位代码、白名单命中的裸大写串。**白名单而不是黑名单** —— 黑名单没想到禁止 "MongoDB"，于是它成了 MDB |
| `logistics` | 运单号、集装箱号 | UPS `1Z`+16、顺丰 `SF`+12~15 是独占前缀；集装箱号**重算 ISO 6346 校验位**。裸数字串只报 0.3，低于阈值 |
| `payments` | Stripe 对象 | 带类型前缀的 id（`ch_` 收款、`pi_` 支付意图、`cus_` 客户…）。句子里不会有别的东西长成 `pi_3PqR8s2eZvKYlo2C0aBcDeFg` |
| `realty` | 某个城市的住房市场 | 城市名**加上**住房词。城市名单独出现只报 0.3 —— 「杭州」出现在出差计划、地址解析、机房区域里 |

### 三种不同的难度，同一条规则

`payments` 是最干净的：前缀让识别本身就是解析，所以它可以永久注册而零成本。

`logistics` 的标识符几乎全是数字，和发票号、订单号、毫秒时间戳同形。所以它靠**可重算的
校验位**把猜测变成解析 —— `CSQU3054383` 校验通过（0.95），`MSKU1234567` 不通过（0.3）。

`realty` 是最难的一个，值得单独读：它的领域里**没有**标识符。所以它解析的实体不是城市，
而是「这个城市的住房市场」，需要两样东西一起出现才能命名。这和 finance 的做法方向相反 ——
finance 要**降级**一个长得像标识符的形状，realty 要**组合**两个都不是标识符的东西。

`realty` 也是最后一个从「每条消息都走一遍」的确定性链里搬进 pack 的 handler。
搬进来之后，「这个物业管理系统怎么改」不再会被回答成房价指数。

## 绿灯定义：pack 接进闭环的地方

每个行业对「对」的定义都不同，但表达方式是同一个：**一条错了就非零退出的命令**。

所以 pack 不需要把它那一行的知识教给核心，它只需要说清楚这一行的绿灯长什么样，
[验收闸门](acceptance-gate.md)就会自动替它守住。

pack 知道领域的形状（运单是运单，`ch_…` 是一笔收款），但它不可能知道**某一家公司**
怎么检查这些东西上的工作做对了 —— 哪个脚本对账、哪个作业校验 schema、他们的构建叫什么。
这个知识属于工作区，不属于 pack。所以它从项目自己的 `.ariarc` 里读：

```jsonc
{
  "acceptance": {
    "default":   ["python3 -m pytest -q"],
    "logistics": ["python3 scripts/reconcile_waybills.py"],
    "payments":  ["python3 scripts/verify_stripe_sync.py"]
  }
}
```

`default` 对每一轮生效。以 pack 命名的那一组，**只在该 pack 从这条消息里解析出实体时生效** ——
和 pack 贡献的其他一切走同一条激活规则，所以声明一条物流检查不会拖慢一个支付问题。

优先级（从最具体开始）：

1. session 配置里的 `acceptance_commands` —— 用户为这次会话明确说过，不被覆盖
2. 激活的 pack 声明的命令
3. 工作区的 `acceptance.default`
4. 都没有 → 回落到从改动文件推断，也就是这一切存在之前的行为

**这就是新增一个行业的完整产品化路径**：一家公司不需要为了接入而扩展 Aria，
他们声明绿灯是什么，然后已经存在的那个闭环开始替他们守住这条线。

## 加一个 pack

```python
class ClinicalPack(BaseDomainPack):
    name = "clinical"

    def resolve_entities(self, message):
        # 便宜、不做网络 I/O —— 每条消息、每个 pack 都会跑到这里。
        # 不确定就返回空:漏激活可以补救,错激活会静默地回答另一个问题。
        ...

    def tool_names(self):        return ("lookup_icd", ...)
    def prompt_fragment(self, activation): return "..."
    def acceptance_commands(self, activation):
        from aria_code.packs import rules
        return rules.acceptance_commands_for("clinical")
```

然后在 `packs/__init__.py` 的 `load_builtin_packs()` 里加上模块名。每个 pack 单独
try/except 加载：一个 pack 坏掉的代价必须是这个 pack，而不是整个会话。
