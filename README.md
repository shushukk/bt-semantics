# btfsv — 行为树的形式语义建模与 SMT 验证

> 课程结课报告（工程实践 · B 自命题型）配套 Demo。
> 一句话：**给行为树的 Tick 语义一套严格的操作语义（SOS），把它抽取成有限状态机，再用 z3 做有界模型检测，并把反例回放到解释器上复核。**

行为树（Behavior Tree, BT）在游戏 AI 和机器人里用得极广，但"节点返回 RUNNING 时谁记住什么"这件事，
各家引擎（UE4、Unity、Behavior Designer、py_trees）的规定并不一致：并行节点怎么算成功、低优先级分支运行时
高优先级条件要不要重新检查、动作被中断后计数器从几开始 —— 只要有一处不一致，同一棵树换引擎就会出 bug。
本项目用**形式语义**把这些细节钉死，并给出一个能自动找 bug 的验证流程。

---

## 1. 流水线

```
JSON 行为树描述
   │  loader.py      解析 + 静态检查（节点类型 / id 唯一 / 性质里引用的原子命题与动作必须存在）
   ▼
Node 树 + 记忆 μ
   │  tree.py        Tick 解释器（SOS 规则的可执行版本，见 §3）
   ▼
显式状态空间探索
   │  fsm.py         状态 = 记忆快照 μ；输入 = 环境 σ；输出 = 本 tick 触发的事件 E
   ▼
有限状态机（δ : S × Σ → S × 𝒫(Action)）
   │  z3_verify.py   有界模型检测：把 k 步展开成 SMT 公式，找违反性质的环境序列
   ▼
反例轨迹（环境序列 + 状态序列 + 事件序列） ──► 回放解释器复核（不盲信求解器）
```

## 2. 快速开始

```bash
python3 -m venv .venv && source .venv/bin/activate     # 需要 Python ≥ 3.10
pip install -r requirements.txt                        # z3-solver, pytest

# 1) 看树结构、原子命题、性质
python -m btfsv show examples/guard.json

# 2) 按环境脚本模拟执行（每个 --env 是一个 tick 的环境，可重复）
python -m btfsv run examples/guard.json --env "see_enemy=true" --ticks 6

# 3) 抽取有限状态机（顺带导出 DOT 画图）
python -m btfsv explore examples/buggy.json --dot build/buggy.dot
dot -Tsvg build/buggy.dot -o build/buggy.svg

# 4) 有界模型检测：性质成立 / 找出反例
python -m btfsv verify examples/guard.json
python -m btfsv verify examples/buggy.json

# 5) 导出 SOS 推理规则（写报告直接用）
python -m btfsv rules --format latex    --out build/rules.tex
python -m btfsv rules --format markdown --out build/rules.md
python -m btfsv rules --analogy         # 附带"属性文法类比"表

# 6) 测试 + 规模基准
python -m pytest -q
python scripts/bench_scale.py           # 输出 build/bench.{csv,md,html}
```

## 3. 语义：SOS 规则与实现一一对应

配置写作 ⟨n, μ⟩，单步转移：

```
⟨n, μ⟩ --σ--> ⟨n', μ'⟩ : st, E        st ∈ {S, F, R},  E ⊆ Action
```

`python -m btfsv rules` 会打印全部 14 条规则，几条关键规则（与 `btfsv/tree.py` 的分支一一对应）：

```
[A-END]   c + 1 = d                                     ← Action.tick
          ─────────────────────────────
          ⟨act a[c], μ⟩ ↓ S, μ[c ↦ 0], {start(a)}

[S-SEQ-RUN]  ⟨c_i, μ⟩ → R, μ', E                        ← Sequence.tick 的 RUNNING 分支
             ───────────────────────────
             ⟨seq[c₁..cₙ][i], μ⟩ → R, μ'[i ↦ i], E

[S-SEQ-F]    ⟨c_i, μ⟩ ↓ F, μ', E                        ← Sequence.tick 的 FAILURE 分支
             ─────────────────────────
             ⟨seq[c₁..cₙ][i], μ⟩ ↓ F, μ'[i ↦ 0], E

[S-PAR-F]    |{j | st_j = F}| > n − k                   ← Parallel.tick 的"失败优先"
             ────────────────────────
             ⟨par^k[c₁..cₙ], μ⟩ ↓ F, ⋃_j E_j
```

**核心不变量（论文 3.2 节 Lemma 1）**：节点返回 S 或 F 时把自己的记忆清零，因此 μ 只保存
"正在 RUNNING 的那条路径"上的信息。它是后面状态空间**有限**的唯一理由，由
`tests/test_tree.py::test_memory_reset_after_terminal` 固定住。

### 与属性文法的对应（编译原理视角）

| 属性文法 | 行为树 Tick | 说明 |
|---|---|---|
| 继承属性 | 环境 σ | 自上而下传递，子节点只读 |
| 综合属性 | 子节点状态 st | 自下而上返回，父节点据此定值 |
| 属性求值 | 一次 tick | 单遍、自底向上；RUNNING 相当于"求值挂起" |
| 增量/惰性求值 | Sequence/Selector 的续跑指针 i | 下次从断点继续，不重复求值已成功的子树 |
| 不动点迭代 | 反复 tick 直到根节点终止 | 整棵树执行 = tick 的最小不动点 |

## 4. 性质语言

| 记法 | 代码 | 编码成"存在反例"的条件 |
|---|---|---|
| `G φ` | `always` | ∃t. ¬φ(t) |
| `G ¬φ` | `never` | ∃t. φ(t) |
| `G (φ → F≤k ψ)` | `response(φ, ψ, within=k, sustain=s)` | ∃t. φ(t)∧…∧φ(t+s−1) ∧ ⋀_{i=0..k} ¬ψ(t+i) |
| `F φ` | `reach` | ∃t. φ(t)（sat 即"可达"，是见证不是反例） |

谓词：`{"atom": "see_enemy"}`（环境）、`{"event": "flee"}`（动作在某个 tick 被触发）、
`not / and / or / implies / const`。

两个刻意的设计：

1. **`sustain` 参数**：`sustain=2` 表示"条件连续成立 2 个 tick 才触发性质"。
   这不是花哨，而是必需的——否则"看见敌人就要逃跑"在敌人下一秒消失时会被判为违反，
   得到的反例没有工程意义。
2. **窗口必须完整落在 horizon 内**：否则最后几个 tick 会把"来不及验证"误判成反例。
   这个坑实际踩过，见 `Encoder.query` 的注释。

## 5. 两个例子

### 5.1 `examples/guard.json` —— 三条性质都成立

```
[P1 反应性]  G (see_enemy[持续 2 tick] → F≤2 ⟨flee⟩)     结论: K 步内有界验证通过 (unsat)
[P2 无死锁]  G (true → F≤3 (⟨flee⟩∨⟨heal⟩∨⟨patrol⟩))    结论: K 步内有界验证通过 (unsat)
[P3 可达性]  F (⟨heal⟩)                                  结论: 可达 (sat，附见证轨迹)
```

### 5.2 `examples/buggy.json` —— 一个真实的 starvation bug

树里 `heal` 分支的守卫被 `Inverter` 写反了（满血时反而去治疗），于是 `heal_seq` 永远成功、
永远抢占高优先级，`flee` 再也执行不到：

```
[P1 反应性]  结论: 发现反例（性质不成立）
 tick | 环境                 | 根状态 | 事件
 0    | low_hp=F see_enemy=T | 运行   | heal    ← 反例窗口起点
 1    | low_hp=F see_enemy=T | 运行   | -
 2    | low_hp=F see_enemy=F | 运行   | -
 ...
 反例起点: tick 0 —— 此时 see_enemy 成立，但此后 2 个 tick 内 ⟨flee⟩ 始终未发生
 回放复核: 通过（具体语义确认该轨迹）
[P4 互斥]   G ¬(⟨flee⟩ ∧ ⟨heal⟩)   结论: 有界验证通过（同一个 bug 不妨碍这条性质成立）
```

注意 P4 依然通过 —— 模型检测既能找 bug，也能给出"有界范围内没问题"的结论，两者不矛盾。

## 6. 规模与算力规划

`python scripts/bench_scale.py` 的实测（Apple Silicon，单线程；状态上限 10 万、转移边上限 15 万）：

| w | 动作时长 | 环境数 | 状态数 | 转移边 | 抽取(s) | BMC 结论 | 求解(s) |
|---:|---|---:|---:|---:|---:|---|---:|
| 1 | 2 | 2 | 2 | 4 | 0.00 | 通过 | 0.005 |
| 2 | 2,3 | 4 | 6 | 24 | 0.00 | 通过 | 0.006 |
| 3 | 2,3,4 | 8 | 24 | 192 | 0.00 | 通过 | 0.018 |
| 4 | 2,3,4,2 | 16 | 48 | 768 | 0.01 | 通过 | 0.374 |
| 5 | 2,3,4,2,3 | 32 | 144 | 4,608 | 0.04 | 通过 | 0.585 |
| 6 | 2,3,4,2,3,4 | 64 | 576 | 36,864 | 0.39 | **跳过（规模）** | – |
| 4 | 4,5,6,4 | 16 | 480 | 7,680 | 0.06 | 通过 | 0.648 |
| 5 | 4,5,6,4,5 | 32 | 2,400 | 76,800 | 0.70 | **跳过（规模）** | – |
| 6 | 4,5,6,4,5,6 | 64 | >15 万边 | – | 1.63 | **撞上限** | – |

三个维度同时爆炸，而且乘法叠加：

```
|S|  ≈ Π d_i          并行分支的动作进度可以任意组合
|Σ|  =  2^w            每个分支一个守卫原子命题
|δ|  =  |S| × 2^w      真正先撞墙的是它
编码 ≈  |δ| · k        BMC 的公式规模
```

从 144 个状态（4 608 条边）到 2 400 个状态（76 800 条边），状态数涨 17 倍，
单机 BMC 的编码已经超出预算；到 8~10 个分支（|S|≈7k~1.7e5、|δ|≈1.8e6~1.7e8）就完全不是单机能碰的量级了。

**云端算力需求（报告里可据此展开）**

1. **纵向（先把单机榨干）**：缩小环境抽象（等价类合并原子命题）、缩短 horizon、
   用符号化表示（BDD/SMT with uninterpreted functions 而非显式 FSM）。
2. **横向（真正的算力需求）**：状态空间按"顶层子树组合"切块，多机并行 BFS 再合并；
   `--state-limit / --transition-limit` 就是预算控制点，超限抛 `StateExplosion`，
   天然可以做成"分片 + 调度"的任务模型。
3. **预算口径**：每条转移约 100~200 B 显式存储，10^8 条边 ≈ 10~20 GB —— 分布式是唯一出路。

## 7. 目录结构

```
bt-semantics/
├── btfsv/
│   ├── status.py       三值语义（F/R/S）
│   ├── tree.py         节点 + Tick 解释器（SOS 的可执行版本）
│   ├── loader.py       JSON → 树，静态检查
│   ├── sos.py          SOS 规则表 + text/markdown/latex 渲染 + 属性文法类比
│   ├── fsm.py          显式状态空间探索、FSM 抽取、反例回放
│   ├── properties.py   性质 AST（two-way：可回放求值 / 可编码成 z3）
│   ├── z3_verify.py    BMC 编码、求解、反例提取与复核
│   └── cli.py          子命令：show / run / explore / verify / rules
├── examples/           guard.json（性质成立）、buggy.json（starvation bug）
├── scripts/bench_scale.py   规模基准（CSV / Markdown / HTML 图表）
├── tests/              41 个单元测试（语义、解析、FSM 一致性、验证正确性）
├── cloud_demo.ipynb    云端一键预演（Jupyter：模拟→explore→verify→状态爆炸曲线）
├── docs/REPORT.md      报告章节 ↔ 代码/命令 的对应表、算力规划模板、答辩问答
├── docs/CLOUD.md       AMD AUP Learning Cloud 预演指南（步骤/证据清单/算力需求模板）
├── docs/figures/       讲解与报告配图（SVG + PNG）
└── build/              自动生成的基准数据与图表
```

## 8. 局限与未来工作

* **有界结论**：unsat 只说明"k 步内无反例"，不是完全证明；后续可做 k-induction 或不变式合成。
* **并行节点无记忆**：已完成的子节点在下一 tick 会被重新推进（与经典 BT 一致），
  这会让某些动作重复触发；若要支持"并行 + 每个子节点只执行一次"，需要给 `Parallel` 加完成集合记忆。
* **没有抢占**：带记忆的 Selector 在低优先级分支 RUNNING 时不会回看高优先级条件
  （`guard.json` 把 P1 的 `sustain` 改成 1 就能看到这条语义的直接后果）。这是语义选择，不是 bug。
* **无数据流**：尚未建模黑板（blackboard）变量与参数化动作，语义上是无状态的原子命题。
* **编码效率**：`event_bit` 用"逐状态析取"编码，|δ| 大时公式膨胀很快；
  交叉验证可以导出 SMV 交给 NuSMV（原生 CTL/LTL、BDD），或改用 `z3.Function` 编码。

## 9. 参考

* M. Colledanchise, P. Ögren. *Behavior Trees in Robotics and AI: An Introduction.* CRC Press, 2018.
* G. D. Plotkin. *A Structural Approach to Operational Semantics.* 1981.
* H. R. Nielson, F. Nielson. *Semantics with Applications: A Formal Introduction.* 1992.
* L. de Moura, N. Bjørner. *Z3: An Efficient SMT Solver.* TACAS 2008.
* A. Cimatti et al. *NuSMV 2: An OpenSource Tool for Symbolic Model Checking.* CAV 2002.
* D. E. Knuth. *Semantics of Context-Free Languages.* (属性文法, 1968)

---

**复现环境**：Python 3.13 + z3-solver 5.3.1（`requirements.txt` 只锁下限，4.12+ 均可）。
全部 41 个测试 + 18 组规模实验在单机上约 1 分钟跑完，峰值内存约 180 MB。
