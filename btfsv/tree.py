"""行为树节点与 Tick 解释器（SOS 规则的可执行版本）。

配置（configuration）定义为 :math:`\\langle n, \\mu \\rangle`：

* :math:`n` —— 语法树（节点及其子节点）；
* :math:`\\mu` —— 记忆映射，节点只保存"运行中"的中间状态。

单个 tick 的转移记作 :math:`\\langle n,\\mu\\rangle \\xrightarrow{\\sigma} \\langle n',\\mu'\\rangle : st, E`，
其中 :math:`\\sigma` 是环境（原子命题赋值），:math:`st \\in \\{F,R,S\\}`，:math:`E` 是本 tick 触发的事件集合。

关键不变量（论文 3.2 节 Lemma 1）
----------------------------------
节点返回 S 或 F 时把自己的记忆清零，因此 :math:`\\mu` 只可能保存"正在 RUNNING 的路径"上的信息。
该不变量保证显式状态空间有限，也是后续 FSM 抽取与 BMC 可行的前提。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Iterator, Mapping, Sequence

from .properties import Property
from .status import Status


class TickContext:
    """一次 tick 的上下文：收集本 tick 内被触发（started）的动作事件。"""

    __slots__ = ("index", "events")

    def __init__(self, index: int = 0):
        self.index = index
        self.events: list[str] = []

    def emit(self, name: str) -> None:
        self.events.append(name)

    def __repr__(self) -> str:
        return f"TickContext(index={self.index}, events={self.events})"


class Node:
    """节点基类。

    子类需要实现 ``tick``；若节点带记忆，还要实现 ``snapshot`` / ``restore`` 以便状态空间探索。
    """

    kind = "node"

    def __init__(self, node_id: str, children: Sequence["Node"] = ()):
        self.id = str(node_id)
        self._children: tuple[Node, ...] = tuple(children)

    # ---------------- 结构 ----------------
    @property
    def children(self) -> tuple["Node", ...]:
        return self._children

    def walk(self) -> Iterator["Node"]:
        yield self
        for c in self._children:
            yield from c.walk()

    def label(self) -> str:
        return f"{self.kind}:{self.id}"

    # ---------------- 语义 ----------------
    def tick(self, env: Mapping[str, bool], ctx: TickContext) -> Status:
        raise NotImplementedError

    def reset(self) -> None:
        for c in self._children:
            c.reset()

    # ---------------- 记忆快照 ----------------
    def snapshot(self) -> tuple:
        return tuple(c.snapshot() for c in self._children)

    def restore(self, snap: tuple) -> None:
        for c, s in zip(self._children, snap):
            c.restore(s)

    def to_dict(self) -> dict:
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<{self.label()}>"


# --------------------------------------------------------------------------- #
# 叶子节点
# --------------------------------------------------------------------------- #
class Condition(Node):
    """条件节点：读取环境，无记忆、不含 RUNNING。"""

    kind = "condition"

    def __init__(self, node_id: str, atom: str):
        super().__init__(node_id, ())
        self.atom = str(atom)

    def tick(self, env, ctx) -> Status:
        return Status.SUCCESS if env.get(self.atom, False) else Status.FAILURE

    def label(self) -> str:
        return f"cond:{self.atom}"

    def to_dict(self) -> dict:
        return {"id": self.id, "type": "condition", "atom": self.atom}


class Action(Node):
    """动作节点：需要 ``duration`` 个 tick 才成功，开始执行的 tick 触发事件。"""

    kind = "action"

    def __init__(self, node_id: str, name: str, duration: int = 1):
        super().__init__(node_id, ())
        if duration < 1:
            raise ValueError(f"动作 {name} 的 duration 必须 ≥ 1")
        self.name = str(name)
        self.duration = int(duration)
        self._count = 0

    def tick(self, env, ctx) -> Status:
        if self._count == 0:
            ctx.emit(self.name)  # 事件：动作被触发（开始执行）
        self._count += 1
        if self._count >= self.duration:
            self._count = 0  # 终止即清零 —— 见模块文档的不变量
            return Status.SUCCESS
        return Status.RUNNING

    def reset(self) -> None:
        self._count = 0

    def snapshot(self) -> tuple:
        return (self._count,)

    def restore(self, snap: tuple) -> None:
        self._count = snap[0]

    def label(self) -> str:
        return f"act:{self.name}[d={self.duration}]"

    def to_dict(self) -> dict:
        return {"id": self.id, "type": "action", "name": self.name, "duration": self.duration}


# --------------------------------------------------------------------------- #
# 控制流节点
# --------------------------------------------------------------------------- #
class Sequence(Node):
    """顺序节点（带记忆）：从左到右执行，全部成功才成功。"""

    kind = "sequence"

    def __init__(self, node_id: str, children: Sequence[Node]):
        super().__init__(node_id, children)
        if not children:
            raise ValueError(f"Sequence 节点 {node_id} 至少需要一个子节点")
        self._idx = 0

    def tick(self, env, ctx) -> Status:
        while self._idx < len(self._children):
            st = self._children[self._idx].tick(env, ctx)
            if st == Status.RUNNING:
                return Status.RUNNING
            if st == Status.FAILURE:
                self._idx = 0
                return Status.FAILURE
            self._idx += 1
        self._idx = 0
        return Status.SUCCESS

    def reset(self) -> None:
        self._idx = 0
        super().reset()

    def snapshot(self) -> tuple:
        return (self._idx, super().snapshot())

    def restore(self, snap: tuple) -> None:
        self._idx, child_snap = snap
        super().restore(child_snap)

    def to_dict(self) -> dict:
        return {"id": self.id, "type": "sequence", "children": [c.to_dict() for c in self._children]}


class Selector(Node):
    """选择（回退）节点（带记忆）：从左到右尝试，任一成功即成功。"""

    kind = "selector"

    def __init__(self, node_id: str, children: Sequence[Node]):
        super().__init__(node_id, children)
        if not children:
            raise ValueError(f"Selector 节点 {node_id} 至少需要一个子节点")
        self._idx = 0

    def tick(self, env, ctx) -> Status:
        while self._idx < len(self._children):
            st = self._children[self._idx].tick(env, ctx)
            if st == Status.RUNNING:
                return Status.RUNNING
            if st == Status.SUCCESS:
                self._idx = 0
                return Status.SUCCESS
            self._idx += 1
        self._idx = 0
        return Status.FAILURE

    def reset(self) -> None:
        self._idx = 0
        super().reset()

    def snapshot(self) -> tuple:
        return (self._idx, super().snapshot())

    def restore(self, snap: tuple) -> None:
        self._idx, child_snap = snap
        super().restore(child_snap)

    def to_dict(self) -> dict:
        return {"id": self.id, "type": "selector", "children": [c.to_dict() for c in self._children]}


class Parallel(Node):
    """并行节点：每个 tick 推进所有子节点，达到 ``k`` 个成功即成功。

    失败优先级高于成功：只要 ``fail > n - k``，就不可能再凑够 k 个成功，立即失败。

    注：与经典行为树一致，并行节点不带记忆，已完成的子节点在下一 tick 会被重新推进。
    这一点在论文 7 节的"局限"中讨论。
    """

    kind = "parallel"

    def __init__(self, node_id: str, children: Sequence[Node], k: int | None = None):
        super().__init__(node_id, children)
        if not children:
            raise ValueError(f"Parallel 节点 {node_id} 至少需要一个子节点")
        self.k = len(children) if k is None else int(k)
        if not 1 <= self.k <= len(children):
            raise ValueError(f"Parallel 节点 {node_id} 的 k 必须在 [1, {len(children)}] 内")

    def tick(self, env, ctx) -> Status:
        succ = fail = 0
        for c in self._children:
            st = c.tick(env, ctx)
            if st == Status.SUCCESS:
                succ += 1
            elif st == Status.FAILURE:
                fail += 1
        if fail > len(self._children) - self.k:
            return Status.FAILURE
        if succ >= self.k:
            return Status.SUCCESS
        return Status.RUNNING

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": "parallel",
            "k": self.k,
            "children": [c.to_dict() for c in self._children],
        }


class Inverter(Node):
    """装饰节点：交换 S / F，RUNNING 透传。"""

    kind = "inverter"

    def __init__(self, node_id: str, child: Node):
        super().__init__(node_id, (child,))

    def tick(self, env, ctx) -> Status:
        st = self._children[0].tick(env, ctx)
        if st == Status.SUCCESS:
            return Status.FAILURE
        if st == Status.FAILURE:
            return Status.SUCCESS
        return Status.RUNNING

    def to_dict(self) -> dict:
        return {"id": self.id, "type": "inverter", "child": self._children[0].to_dict()}


# --------------------------------------------------------------------------- #
# 整棵树
# --------------------------------------------------------------------------- #
@dataclass
class Tree:
    """一棵行为树 + 它的元信息（原子命题、动作、待验证性质）。"""

    name: str
    root: Node
    atoms: tuple[str, ...]
    actions: tuple[str, ...]
    properties: tuple[Property, ...] = ()
    source: dict = field(default_factory=dict, repr=False)

    # ---------------- 语义 ----------------
    def tick(self, env: Mapping[str, bool], index: int = 0) -> tuple[Status, list[str]]:
        ctx = TickContext(index)
        st = self.root.tick(env, ctx)
        return st, ctx.events

    def reset(self) -> None:
        self.root.reset()

    # ---------------- 状态快照 ----------------
    def snapshot(self) -> tuple:
        return self.root.snapshot()

    def restore(self, snap: tuple) -> None:
        self.root.restore(snap)

    @property
    def initial_state(self) -> tuple:
        self.reset()
        return self.snapshot()

    # ---------------- 查询 ----------------
    def nodes(self) -> list[Node]:
        return list(self.root.walk())

    def find(self, node_id: str) -> Node:
        for n in self.root.walk():
            if n.id == node_id:
                return n
        raise KeyError(f"找不到节点 {node_id}")

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "root": self.root.to_dict(),
            "properties": [p.to_dict() for p in self.properties],
        }

    def __str__(self) -> str:  # pragma: no cover - 调试用
        lines: list[str] = []

        def rec(n: Node, depth: int) -> None:
            lines.append("  " * depth + n.label())
            for c in n.children:
                rec(c, depth + 1)

        rec(self.root, 0)
        return "\n".join(lines)
