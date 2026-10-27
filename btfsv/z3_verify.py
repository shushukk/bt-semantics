"""基于 z3 的有界模型检测（BMC, Bounded Model Checking）。

整体思路（对应论文第 5 章）：

1. 把 FSM 展开成 k+1 个"时间步"上的状态变量 :math:`s_0 \\ldots s_k`（整数，取值 ``0..N-1``）;
2. 环境输入 :math:`\\sigma_t` 用布尔变量表示，**不加任何约束** —— 等价于"对所有环境序列全称量化"；
3. 转移关系用嵌套 ``If`` 编码，动作事件用"在哪个源状态 + 哪个输入下被触发"的析取式编码；
4. 把"性质被违反"编码成可满足性查询：

   * ``always φ``        →  ∃t. ¬φ(t)
   * ``never φ``         →  ∃t. φ(t)
   * ``response φ ψ k``  →  ∃t. φ(t) ∧ ⋀_{i=0..k} ¬ψ(t+i)
   * ``reach φ``         →  ∃t. φ(t)（找到即"可达"，是见证而非反例）

   **sat** 表示找到反例（附具体轨迹），**unsat** 表示该 horizon 内无反例（有界结论，不等于完全证明）。
5. 反例不是直接信任 SMT 的解，而是把环境序列**回放到解释器**上重新跑一遍，用具体语义复核（``confirmed``）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Sequence

import z3

from .fsm import Machine, Step
from .properties import Property
from .tree import Tree

STATUS_ZH = {
    "violated": "发现反例（性质不成立）",
    "holds_within_k": "K 步内有界验证通过",
    "reachable": "可达（给出见证轨迹）",
    "unreachable_within_k": "K 步内不可达",
    "unknown": "求解超时/未知",
    "skipped": "超出编码预算，已跳过",
}


class Encoder:
    """把 FSM + 性质编码成 z3 公式。"""

    def __init__(self, machine: Machine, horizon: int):
        self.m = machine
        self.K = horizon
        self.constraints: list[z3.BoolRef] = []
        self.s: list[z3.ArithRef] = [z3.Int(f"s_{t}") for t in range(horizon + 1)]
        self._env: dict[str, list[z3.BoolRef]] = {
            a: [z3.Bool(f"e_{a}_{t}") for t in range(horizon + 1)] for a in machine.atoms
        }
        self._event: dict[tuple[str, int], z3.BoolRef] = {}
        self.branch_count = 0

    # ---------------- 基础构造 ----------------
    def bool_const(self, value: bool):
        return z3.BoolVal(value)

    def not_(self, x):
        return z3.Not(x)

    def and_(self, xs: Sequence):
        xs = list(xs)
        return z3.And(*xs) if xs else z3.BoolVal(True)

    def or_(self, xs: Sequence):
        xs = list(xs)
        return z3.Or(*xs) if xs else z3.BoolVal(False)

    def implies(self, a, b):
        return z3.Implies(a, b)

    def env_bit(self, name: str, t: int):
        if name not in self._env:
            raise KeyError(f"未知原子命题 {name!r}（树中可用: {sorted(self._env)}）")
        return self._env[name][t]

    def env_match(self, env_index: int, t: int):
        env = self.m.envs[env_index]
        parts = [
            (self._env[a][t] if env[a] else z3.Not(self._env[a][t])) for a in self.m.atoms
        ]
        return z3.And(*parts) if parts else z3.BoolVal(True)

    def event_bit(self, name: str, t: int):
        key = (name, t)
        if key in self._event:
            return self._event[key]
        var = z3.Bool(f"ev_{name}_{t}")
        terms = [
            z3.And(self.s[t] == tr.src, self.env_match(tr.env, t))
            for tr in self.m.transitions
            if name in tr.events
        ]
        self.branch_count += len(terms)
        self.constraints.append(var == z3.Or(*terms) if terms else z3.Not(var))
        self._event[key] = var
        return var

    # ---------------- 具体语义编码 ----------------
    def state_domains(self) -> None:
        n = self.m.num_states
        self.constraints.append(self.s[0] == self.m.start)  # 初始状态必须钉死
        for t in range(self.K + 1):
            self.constraints.append(self.s[t] >= 0)
            self.constraints.append(self.s[t] < n)

    def transition_relation(self) -> None:
        """s_{t+1} = δ(s_t, σ_t)，按状态分块用嵌套 If 编码。"""
        for t in range(self.K):
            for i in range(self.m.num_states):
                outs = self.m.outgoing(i)
                if not outs:
                    continue
                expr = z3.IntVal(outs[-1].dst)
                for tr in reversed(outs[:-1]):
                    expr = z3.If(self.env_match(tr.env, t), z3.IntVal(tr.dst), expr)
                self.branch_count += len(outs)
                self.constraints.append(z3.Implies(self.s[t] == i, self.s[t + 1] == expr))

    # ---------------- 性质编码 ----------------
    def query(self, prop: Property):
        K = self.K
        if prop.kind in ("always", "never"):
            alts = []
            for t in range(K + 1):
                b = prop.body.encode(self, t)
                alts.append(z3.Not(b) if prop.kind == "always" else b)
            return z3.Or(*alts)
        if prop.kind == "response":
            alts = []
            sustain = max(1, int(prop.sustain))
            # 只检查"完整窗口仍落在 horizon 内"的起点，否则尾部截断会产生假反例
            last = min(K - prop.within, K - sustain + 1)
            for t in range(max(0, last) + 1):
                trig = z3.And(*[prop.trigger.encode(self, t + i) for i in range(sustain)])
                no_target = [
                    z3.Not(prop.target.encode(self, i)) for i in range(t, t + prop.within + 1)
                ]
                alts.append(z3.And(trig, *no_target))
            if not alts:
                return z3.BoolVal(False)
            return z3.Or(*alts)
        if prop.kind == "reach":
            return z3.Or(*[prop.body.encode(self, t) for t in range(K + 1)])
        raise ValueError(f"未知性质类型: {prop.kind}")


@dataclass
class VerifyResult:
    property: Property
    status: str
    horizon: int
    steps: list[Step] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    message: str = ""
    confirmed: bool = False
    bad_index: int | None = None

    @property
    def sat(self) -> bool:
        return self.status in ("violated", "reachable")

    @property
    def status_zh(self) -> str:
        return STATUS_ZH.get(self.status, self.status)


def verify(
    machine: Machine,
    prop: Property,
    *,
    tree: Tree | None = None,
    horizon: int | None = None,
    timeout_ms: int = 60_000,
    encoding_budget: int = 3_000_000,
) -> VerifyResult:
    """对单条性质做有界模型检测。"""
    K = int(horizon if horizon is not None else prop.horizon)
    est = machine.num_transitions * max(K, 1) + machine.num_states * max(K, 1)
    if est > encoding_budget:
        return VerifyResult(
            property=prop,
            status="skipped",
            horizon=K,
            stats={"estimated_terms": est, "budget": encoding_budget},
            message=f"编码规模估计 {est:,} 项 > 预算 {encoding_budget:,}；请降低 horizon 或换用 NuSMV 等符号化工具",
        )

    t0 = time.perf_counter()
    enc = Encoder(machine, K)
    enc.state_domains()
    enc.transition_relation()
    query = enc.query(prop)
    t_enc = time.perf_counter() - t0

    solver = z3.Solver()
    solver.set("timeout", int(timeout_ms))
    solver.add(*enc.constraints)
    solver.add(query)

    t1 = time.perf_counter()
    res = solver.check()
    t_solve = time.perf_counter() - t1

    stats = {
        "states": machine.num_states,
        "transitions": machine.num_transitions,
        "horizon": K,
        "constraints": len(enc.constraints),
        "encoding_terms": enc.branch_count,
        "encode_seconds": round(t_enc, 3),
        "solve_seconds": round(t_solve, 3),
    }

    if res == z3.unknown:
        return VerifyResult(prop, "unknown", K, stats=stats, message=solver.reason_unknown())

    if res == z3.unsat:
        status = "unreachable_within_k" if prop.kind == "reach" else "holds_within_k"
        return VerifyResult(prop, status, K, stats=stats, message="unsat：该 horizon 内不存在反例")

    # ---------------- sat：提取环境序列并回放 ----------------
    model = solver.model()

    def val(v) -> bool:
        return z3.is_true(model.eval(v, model_completion=True))

    env_indices: list[int] = []
    for t in range(K + 1):
        env = {a: val(enc._env[a][t]) for a in machine.atoms}
        env_indices.append(machine.envs.index(env))

    steps: list[Step] = []
    bad_index: int | None = None
    confirmed = False
    if tree is not None:
        steps = machine.replay(tree, env_indices)
        bad_index = prop.violation_index(
            [{"env": s.env, "events": s.events} for s in steps]
        )
        confirmed = bad_index is not None
        # 交叉校验：SMT 里的事件变量必须与回放结果一致（防止编码方向写反）
        assigned = {str(d.name()) for d in model.decls()}
        for (name, t), var in enc._event.items():
            if str(var.decl().name()) not in assigned:
                continue
            smt_val = val(var)
            replay_val = name in steps[t].events
            if smt_val != replay_val:
                stats["event_mismatch"] = stats.get("event_mismatch", 0) + 1
        stats["replay_confirmed"] = confirmed
        stats["replayed_steps"] = len(steps)

    status = "reachable" if prop.kind == "reach" else "violated"
    msg = "sat：找到反例轨迹" if prop.kind != "reach" else "sat：找到可达见证轨迹"
    return VerifyResult(
        property=prop,
        status=status,
        horizon=K,
        steps=steps,
        stats=stats,
        message=msg,
        confirmed=confirmed,
        bad_index=bad_index,
    )


def verify_all(
    machine: Machine,
    tree: Tree,
    *,
    horizon: int | None = None,
    timeout_ms: int = 60_000,
) -> list[VerifyResult]:
    return [
        verify(machine, p, tree=tree, horizon=horizon, timeout_ms=timeout_ms) for p in tree.properties
    ]
