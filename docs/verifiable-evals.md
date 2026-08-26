# 可验证 Eval

> 记分牌只有一个数字，而且这个数字必须是挣来的。

## 之前量的是什么

`evals/run_evals.py` 用两种方式打分：在 transcript 里找字符串，以及问一个裁判模型
「这个回答看起来对吗」。它量的是 agent **说**了什么。它量不到 agent **做**了什么，
而且只有 2 个 case，实际上什么都量不到。

## 现在量什么

一个任务 = 一个工作区 + 一句提示 + 一条命令。分数 = agent 做完之后，那条命令的退出码。

```yaml
- id: reconcile-waybills
  prompt: "承运商对账脚本漏算了燃油附加费…"
  fixture: reconcile_waybills
  verify: "{python} -m pytest -q"
  requires: [pytest]
  tags: [logistics, reconciliation]
```

没有裁判、没有字符串匹配、没有部分分。测试过或者不过，账平或者不平。
一句自信的「任务完成」得零分——这正是重点。

这和[验收闸门](acceptance-gate.md)是同一个形状，原因也一样：每个领域都把「对」表达成
一条错了就非零退出的命令。所以一个物流套件和一个 Python 套件的差别只是那条命令不同，
**给记分牌加一个行业的成本是一个 fixture 加一行 YAML，而不是一套新的打分策略**。

## 让套件可信的那个性质

**每个任务都必须一开始是红的。**

agent 跑之前，harness 先在未改动的 fixture 上跑一遍 `verify`。如果已经绿了，
这个任务报 `INVALID`，既不算通过也不算失败。

这条检查比任何单个任务都值钱。一个悄悄积累「本来就是绿的」任务的套件，会报出一个不断上升的
通过率，同时量到的东西越来越少 —— 而且这个故障**恰恰因为数字好看而不可见**：
你无法从结果里区分「agent 解决了」和「它从来就没坏过」。每次都跑这条检查，
代价是每个任务一条命令，换来的是那个数字有意义。

### 红必须意味着检查真的跑了

预检本身还不够，而这个 harness 的第一次运行就证明了：五个任务全报红，
**五个都是因为 PATH 上的 `python3` 没装 pytest**。套件看起来很健康，实际什么都没量 ——
预检要防的那个「静默注水」，从后门进来了。

两件事堵上它：

- 命令里的 `{python}` 解析成运行 harness 的那个解释器，套件验证的是它被启动时所在的环境，
  而不是这台机器上 `python3` 恰好指向什么。
- 任务可以声明 `requires: [pytest]`。缺模块报 `ERROR`（不计入分数），
  而不是被当成一个红测试指望 agent 去修。

## 四种结果，不是布尔值

| 结果 | 含义 | 计入分数 |
|------|------|---------|
| `PASS` | 检查退出 0 | 是 |
| `FAIL` | 检查退出非 0 | 是 |
| `INVALID` | fixture 一开始就是绿的 —— 这个任务量不到东西 | **否** |
| `ERROR` | 环境缺依赖、fixture 找不到、solver 崩了 | **否** |

「agent 没做到」和「任务本身坏了」需要完全相反的应对，把它们合并就是一个烂掉的套件
不被发现的方式。通过率 = PASS / (PASS + FAIL)：把 INVALID 和 ERROR 算成失败，
一个坏 fixture 会看起来像模型退化；算成通过，它会掩盖一次真的退化。

## 隔离

任务在 fixture 的**副本**里跑，永远不在 fixture 里，也永远不在仓库里。
一个删掉工作区、往外写文件、或者把环境搞坏的 agent，影响的只有它自己那一次运行的目录 ——
这才使得把破坏性任务留在套件里是安全的，而那些恰恰是最值得有的任务。

## 用法

```bash
# 完整跑一遍（需要模型）
python3 -m aria_code.evals.runner evals/suites/core.yaml --report out/score.json

# 只跑某个行业
python3 -m aria_code.evals.runner evals/suites/core.yaml --tag logistics

# 不用模型：只跑预检，确认套件还在量东西
python3 -m aria_code.evals.runner evals/suites/core.yaml --check
```

`--check` 是最值得放进 CI 的那个。它跑每个任务的预检，不需要模型、不需要 API key、
几秒钟出结果，回答的是「这个套件还在量东西吗」。一个漂移成绿色的 fixture 是一次
静默的记分牌注水，`--check` 在造成它的那个 commit 上抓住它，而不是一个月以后。

## 当前套件

`evals/suites/core.yaml` —— 5 个任务，覆盖三个行业：

| 任务 | 标签 | 考什么 |
|------|------|--------|
| `fix-failing-test` | software | 最基础的：定位并修一个真实失败 |
| `off-by-one` | software | 边界条件 + 缺失的入参校验 |
| `missing-arg-validation` | software | 按测试补齐一组校验，且不能改动传入对象 |
| `reconcile-waybills` | logistics | 对账脚本漏算燃油附加费，把正常计费的运单误报为异常 |
| `stripe-refund-validation` | payments | 结算函数接受不属于本期、以及超过原收款金额的退款 |

后两个是重点：它们证明同一套打分机制不需要为新行业改动任何东西。
