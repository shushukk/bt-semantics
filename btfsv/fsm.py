"""显式状态空间探索：把行为树抽取成有限状态机（FSM）。

抽取方式
--------
行为树 + 记忆 μ 构成一个确定性转移系统：

    δ : State × Env → State × 𝒫(Action)
    State = μ（所有节点的记忆快照）

环境 σ 被视为**无约束（敌对）输入**，因此 FSM 的每条边上标注的是"在该环境输入下发生了哪些动作事件"。
对 §State 做 BFS 即可得到全部可达状态；状态数随节点数与动作时长指数增长，
这正是报告"算力规划"一节要量化的现象（见 ``scripts/bench_scale.py``）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from typing import Iterator, Mapping, Sequence

from .status import Status
from .tree import Tree


class StateExplosion(RuntimeError):
    """规模超过预算：说明这台机器的内存/时间已经不够用了。"""

    def __init__(self, limit: int, found: int, what: str = "states"):
        label = "状态数" if what == "states" else "转移边数"
        super().__init__(f"{label}超过上限 {limit:,}（已发现 {found:,} 个）")
        self.limit = limit
        self.found = found
        self.what = what


@dataclass(frozen=True)
class Transition:
    """FSM 的一条边：在状态 ``src`` 下输入环境 ``env``（环境编号），到达 ``dst`` 并触发 ``events``。"""

    src: int
    env: int
    dst: int
    events: tuple[str, ...]


@dataclass
class Step:
    """具体轨迹的一步（用于打印 trace / 回放反例）。"""

    index: int
    env: dict[str, bool]
    status: Status
    events: tuple[str, ...]
    state: int | None = None


def enumerate_envs(atoms: Sequence[str]) -> list[dict[str, bool]]:
    """枚举全部 2^n 个环境赋值（按字典序，编号 0 = 全 false）。"""
    atoms = list(atoms)
    envs: list[dict[str, bool]] = []
    for bits in product([False, True], repeat=len(atoms)):
        envs.append(dict(zip(atoms, bits)))
    return envs


@dataclass
class Machine:
    """抽取出的有限状态机。"""

    atoms: tuple[str, ...]
    actions: tuple[str, ...]
    envs: list[dict[str, bool]]
    states: list[tuple]
    state_index: dict[tuple, int]
    transitions: list[Transition]
    start: int = 0
    _outgoing: dict[int, list[Transition]] = field(default_factory=dict, repr=False)

    # ---------------- 规模 ----------------
    @property
    def num_states(self) -> int:
        return len(self.states)

    @property
    def num_envs(self) -> int:
        return len(self.envs)

    @property
    def num_transitions(self) -> int:
        return len(self.transitions)

    def outgoing(self, state: int) -> list[Transition]:
        if not self._outgoing:
            buckets: dict[int, list[Transition]] = {i: [] for i in range(self.num_states)}
            for tr in self.transitions:
                buckets[tr.src].append(tr)
            self._outgoing = buckets
        return self._outgoing.get(state, [])

    def env_name(self, env_index: int) -> str:
        env = self.envs[env_index]
        on = [k for k, v in env.items() if v]
        return "{" + ",".join(on) + "}" if on else "{}"

    def describe_state(self, tree: Tree, state: int, only_changed: bool = True) -> str:
        """把记忆快照渲染成人类可读的形式，只列出非初始值的分量。"""
        from .tree import Action, Selector, Sequence

        init = self.states[self.start]
        parts: list[str] = []

        def rec(node, snap, init_snap):
            if isinstance(node, Action):
                if snap[0] != init_snap[0] or not only_changed:
                    parts.append(f"{node.id}.c={snap[0]}")
                return
            if isinstance(node, (Sequence, Selector)):
                if snap[0] != init_snap[0] or not only_changed:
                    parts.append(f"{node.id}.i={snap[0]}")
                for c, s, i in zip(node.children, snap[1], init_snap[1]):
                    rec(c, s, i)
                return
            for c, s, i in zip(node.children, snap, init_snap):
                rec(c, s, i)

        rec(tree.root, self.states[state], init)
        return " ".join(parts) if parts else "（初始状态，所有记忆为 0）"

    # ---------------- 轨迹 ----------------
    def replay(self, tree: Tree, env_indices: Sequence[int]) -> list[Step]:
        """按给定的环境序列具体执行一遍（用于反向验证 SMT 给出的反例）。"""
        from .tree import TickContext

        tree.restore(self.states[self.start])
        rows: list[Step] = []
        for t, e_idx in enumerate(env_indices):
            ctx_env = self.envs[e_idx]
            ctx = TickContext(t)
            st = tree.root.tick(ctx_env, ctx)
            rows.append(
                Step(
                    index=t,
                    env=dict(ctx_env),
                    status=st,
                    events=tuple(ctx.events),
                    state=self.state_index.get(tree.snapshot()),
                )
            )
        return rows

    def simulate(self, tree: Tree, envs: Sequence[Mapping[str, bool]]) -> list[Step]:
        tree.restore(self.states[self.start])
        rows: list[Step] = []
        for t, env in enumerate(envs):
            from .tree import TickContext

            ctx = TickContext(t)
            st = tree.root.tick(env, ctx)
            rows.append(
                Step(
                    index=t,
                    env=dict(env),
                    status=st,
                    events=tuple(ctx.events),
                    state=self.state_index.get(tree.snapshot()),
                )
            )
        return rows

    # ---------------- 导出 ----------------
    def to_dot(self, max_states: int = 60) -> str:
        lines = ["digraph bt {", '  rankdir=LR;', '  node [shape=circle, fontname="Helvetica"];']
        for i in range(min(self.num_states, max_states)):
            shape = "doublecircle" if i == self.start else "circle"
            lines.append(f'  s{i} [shape={shape}, label="{i}"];')
        shown = min(self.num_states, max_states)
        for tr in self.transitions:
            if tr.src >= shown or tr.dst >= shown:
                continue
            label = self.env_name(tr.env)
            if tr.events:
                label += " / " + ",".join(tr.events)
            lines.append(f'  s{tr.src} -> s{tr.dst} [label="{label}"];')
        if self.num_states > shown:
            lines.append(f'  note [shape=plaintext, label="仅显示前 {shown}/{self.num_states} 个状态"];')
        lines.append("}")
        return "\n".join(lines)

    def summary(self) -> dict:
        return {
            "states": self.num_states,
            "transitions": self.num_transitions,
            "environments": self.num_envs,
            "atoms": len(self.atoms),
            "actions": len(self.actions),
        }


def build_machine(
    tree: Tree,
    *,
    state_limit: int | None = None,
    transition_limit: int | None = None,
    atom_limit: int = 12,
    max_ticks: int | None = None,
) -> Machine:
    """对行为树做 BFS 状态空间探索，返回抽取出的 FSM。

    ``state_limit`` / ``transition_limit`` 用尽时抛 :class:`StateExplosion`
    （基准测试用它来度量"撞墙"的位置）。
    """
    if len(tree.atoms) > atom_limit:
        raise ValueError(
            f"原子命题有 {len(tree.atoms)} 个，环境组合 2^{len(tree.atoms)} 过大；"
            f"上限为 {atom_limit}（可用 atom_limit 参数放宽）"
        )

    envs = enumerate_envs(tree.atoms)
    tree.reset()
    start_snap = tree.snapshot()

    states: list[tuple] = [start_snap]
    state_index: dict[tuple, int] = {start_snap: 0}
    transitions: list[Transition] = []
    queue: list[int] = [0]

    from .tree import TickContext

    head = 0
    while head < len(queue):
        i = queue[head]
        head += 1
        for e_idx, env in enumerate(envs):
            tree.restore(states[i])
            ctx = TickContext(0)
            tree.root.tick(env, ctx)
            snap = tree.snapshot()
            j = state_index.get(snap)
            if j is None:
                if state_limit is not None and len(states) >= state_limit:
                    raise StateExplosion(state_limit, len(states))
                j = len(states)
                state_index[snap] = j
                states.append(snap)
                queue.append(j)
            transitions.append(Transition(src=i, env=e_idx, dst=j, events=tuple(ctx.events)))
            if transition_limit is not None and len(transitions) >= transition_limit:
                raise StateExplosion(transition_limit, len(transitions), "transitions")

    return Machine(
        atoms=tuple(tree.atoms),
        actions=tuple(tree.actions),
        envs=envs,
        states=states,
        state_index=state_index,
        transitions=transitions,
        start=0,
    )
