"""性质语言：谓词 AST + 时序性质（LTL 的一个受限子集）。

支持的性质形式（对应论文 4.2 节）::

    always(φ)                 G φ                 不安全性质 / 不变式
    never(φ)                  G ¬φ                互斥、边界
    response(φ, ψ, within=k)  G (φ → F_{≤k} ψ)    反应性 / 无死锁
    reach(φ)                  F φ                 可达性（正向见证）

谓词层 ::

    Atom("see_enemy")            环境原子命题（由外部环境提供）
    Event("flee")                动作在某个 tick 被触发（开始执行）
    Not / And / Or / Implies      布尔组合

两个求值方向共用同一棵 AST：
  * ``Pred.eval(env, events)``  —— 在具体轨迹上求值（用于反例回放复核）
  * ``Pred.encode(enc, t)``     —— 编码成 z3 公式（用于符号模型检测）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence


# --------------------------------------------------------------------------- #
# 谓词
# --------------------------------------------------------------------------- #
class Pred:
    """谓词基类。"""

    def atoms(self) -> set[str]:
        return set()

    def actions(self) -> set[str]:
        return set()

    def eval(self, env: dict[str, bool], events: Iterable[str]) -> bool:
        raise NotImplementedError

    def encode(self, enc: Any, t: int) -> Any:
        raise NotImplementedError

    def to_dict(self) -> dict:
        raise NotImplementedError

    # 语法糖：允许直接 p & q / p | q / ~p / p >> q
    def __and__(self, other: "Pred") -> "And":
        return And((self, other))

    def __or__(self, other: "Pred") -> "Or":
        return Or((self, other))

    def __invert__(self) -> "Not":
        return Not(self)

    def __rshift__(self, other: "Pred") -> "Implies":
        return Implies(self, other)


@dataclass(frozen=True)
class Const(Pred):
    value: bool

    def eval(self, env, events) -> bool:
        return self.value

    def encode(self, enc, t):
        return enc.bool_const(self.value)

    def to_dict(self) -> dict:
        return {"const": self.value}

    def __str__(self) -> str:
        return "true" if self.value else "false"


TRUE = Const(True)
FALSE = Const(False)


@dataclass(frozen=True)
class Atom(Pred):
    """环境原子命题，例如 ``see_enemy``。"""

    name: str

    def atoms(self) -> set[str]:
        return {self.name}

    def eval(self, env, events) -> bool:
        return bool(env.get(self.name, False))

    def encode(self, enc, t):
        return enc.env_bit(self.name, t)

    def to_dict(self) -> dict:
        return {"atom": self.name}

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True)
class Event(Pred):
    """动作事件：动作 ``name`` 在该 tick 内被触发（开始执行）。"""

    name: str

    def actions(self) -> set[str]:
        return {self.name}

    def eval(self, env, events) -> bool:
        return self.name in events

    def encode(self, enc, t):
        return enc.event_bit(self.name, t)

    def to_dict(self) -> dict:
        return {"event": self.name}

    def __str__(self) -> str:
        return f"⟨{self.name}⟩"


@dataclass(frozen=True)
class Not(Pred):
    operand: Pred

    def atoms(self):
        return self.operand.atoms()

    def actions(self):
        return self.operand.actions()

    def eval(self, env, events):
        return not self.operand.eval(env, events)

    def encode(self, enc, t):
        return enc.not_(self.operand.encode(enc, t))

    def to_dict(self) -> dict:
        return {"not": self.operand.to_dict()}

    def __str__(self) -> str:
        return f"¬{self.operand}"


@dataclass(frozen=True)
class And(Pred):
    operands: tuple[Pred, ...]

    def __init__(self, operands: Sequence[Pred]):
        object.__setattr__(self, "operands", tuple(operands))

    def atoms(self):
        return set().union(*(o.atoms() for o in self.operands)) if self.operands else set()

    def actions(self):
        return set().union(*(o.actions() for o in self.operands)) if self.operands else set()

    def eval(self, env, events):
        return all(o.eval(env, events) for o in self.operands)

    def encode(self, enc, t):
        return enc.and_([o.encode(enc, t) for o in self.operands])

    def to_dict(self) -> dict:
        return {"and": [o.to_dict() for o in self.operands]}

    def __str__(self) -> str:
        return "(" + " ∧ ".join(str(o) for o in self.operands) + ")"


@dataclass(frozen=True)
class Or(Pred):
    operands: tuple[Pred, ...]

    def __init__(self, operands: Sequence[Pred]):
        object.__setattr__(self, "operands", tuple(operands))

    def atoms(self):
        return set().union(*(o.atoms() for o in self.operands)) if self.operands else set()

    def actions(self):
        return set().union(*(o.actions() for o in self.operands)) if self.operands else set()

    def eval(self, env, events):
        return any(o.eval(env, events) for o in self.operands)

    def encode(self, enc, t):
        return enc.or_([o.encode(enc, t) for o in self.operands])

    def to_dict(self) -> dict:
        return {"or": [o.to_dict() for o in self.operands]}

    def __str__(self) -> str:
        return "(" + " ∨ ".join(str(o) for o in self.operands) + ")"


@dataclass(frozen=True)
class Implies(Pred):
    lhs: Pred
    rhs: Pred

    def atoms(self):
        return self.lhs.atoms() | self.rhs.atoms()

    def actions(self):
        return self.lhs.actions() | self.rhs.actions()

    def eval(self, env, events):
        return (not self.lhs.eval(env, events)) or self.rhs.eval(env, events)

    def encode(self, enc, t):
        return enc.implies(self.lhs.encode(enc, t), self.rhs.encode(enc, t))

    def to_dict(self) -> dict:
        return {"implies": [self.lhs.to_dict(), self.rhs.to_dict()]}

    def __str__(self) -> str:
        return f"({self.lhs} → {self.rhs})"


# --------------------------------------------------------------------------- #
# 时序性质
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Property:
    """一条待验证的时序性质。"""

    name: str
    kind: str  # always | never | response | reach
    body: Pred | None = None
    trigger: Pred | None = None
    target: Pred | None = None
    within: int = 0
    sustain: int = 1
    horizon: int = 6
    description: str = ""

    # ---------------- 构造器 ----------------
    @staticmethod
    def always(name: str, body: Pred, horizon: int = 6, description: str = "") -> "Property":
        return Property(name=name, kind="always", body=body, horizon=horizon, description=description)

    @staticmethod
    def never(name: str, body: Pred, horizon: int = 6, description: str = "") -> "Property":
        return Property(name=name, kind="never", body=body, horizon=horizon, description=description)

    @staticmethod
    def response(
        name: str,
        trigger: Pred,
        target: Pred,
        within: int = 3,
        horizon: int = 6,
        description: str = "",
        sustain: int = 1,
    ) -> "Property":
        return Property(
            name=name,
            kind="response",
            trigger=trigger,
            target=target,
            within=within,
            sustain=sustain,
            horizon=horizon,
            description=description,
        )

    @staticmethod
    def reach(name: str, body: Pred, horizon: int = 6, description: str = "") -> "Property":
        return Property(name=name, kind="reach", body=body, horizon=horizon, description=description)

    # ---------------- 元信息 ----------------
    @property
    def ltl(self) -> str:
        if self.kind == "always":
            return f"G ({self.body})"
        if self.kind == "never":
            return f"G ¬({self.body})"
        if self.kind == "response":
            trig = str(self.trigger)
            if self.sustain > 1:
                trig = f"{trig}[持续 {self.sustain} tick]"
            return f"G ({trig} → F≤{self.within} {self.target})"
        if self.kind == "reach":
            return f"F ({self.body})"
        return "?"

    def atoms(self) -> set[str]:
        out: set[str] = set()
        for p in self._all_preds():
            out |= p.atoms()
        return out

    def actions(self) -> set[str]:
        out: set[str] = set()
        for p in self._all_preds():
            out |= p.actions()
        return out

    def _all_preds(self) -> list[Pred]:
        return [p for p in (self.body, self.trigger, self.target) if p is not None]

    # ---------------- 具体轨迹求值 ----------------
    def violation_index(self, rows: Sequence[dict]) -> int | None:
        """在具体轨迹上寻找反例起点；返回 tick 下标，None 表示该轨迹上未违反。"""
        n = len(rows)
        if self.kind in ("always", "never"):
            for t, row in enumerate(rows):
                env, events = row["env"], row["events"]
                hit = self.body.eval(env, events)
                if self.kind == "always" and not hit:
                    return t
                if self.kind == "never" and hit:
                    return t
            return None
        if self.kind == "response":
            sustain = max(1, int(self.sustain))
            for t in range(n):
                if t + self.within > n - 1:
                    break  # 窗口不完整，与 SMT 编码保持一致（避免尾部假反例）
                if not all(
                    self.trigger.eval(rows[t + i]["env"], rows[t + i]["events"]) for i in range(sustain)
                ):
                    continue
                lo, hi = t, t + self.within
                if not any(self.target.eval(rows[i]["env"], rows[i]["events"]) for i in range(lo, hi + 1)):
                    return t
            return None
        if self.kind == "reach":
            for t, row in enumerate(rows):
                if self.body.eval(row["env"], row["events"]):
                    return t
            return None
        raise ValueError(f"未知性质类型: {self.kind}")

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"name": self.name, "kind": self.kind, "horizon": self.horizon}
        if self.body is not None:
            d["body"] = self.body.to_dict()
        if self.trigger is not None:
            d["trigger"] = self.trigger.to_dict()
        if self.target is not None:
            d["target"] = self.target.to_dict()
        if self.kind == "response":
            d["within"] = self.within
            if self.sustain > 1:
                d["sustain"] = self.sustain
        if self.description:
            d["description"] = self.description
        return d


# --------------------------------------------------------------------------- #
# JSON → AST
# --------------------------------------------------------------------------- #
def pred_from_obj(obj: Any) -> Pred:
    """把 JSON 里的谓词描述翻译成 AST。"""
    if isinstance(obj, bool):
        return Const(obj)
    if isinstance(obj, str):
        # 简写：字符串默认视为环境原子命题
        return Atom(obj)
    if not isinstance(obj, dict):
        raise ValueError(f"无法解析的谓词: {obj!r}")

    keys = set(obj)
    if keys == {"atom"}:
        return Atom(str(obj["atom"]))
    if keys == {"event"}:
        return Event(str(obj["event"]))
    if keys == {"const"}:
        return Const(bool(obj["const"]))
    if keys == {"not"}:
        return Not(pred_from_obj(obj["not"]))
    if keys == {"and"}:
        return And([pred_from_obj(x) for x in obj["and"]])
    if keys == {"or"}:
        return Or([pred_from_obj(x) for x in obj["or"]])
    if keys == {"implies"}:
        lhs, rhs = obj["implies"]
        return Implies(pred_from_obj(lhs), pred_from_obj(rhs))
    raise ValueError(f"无法解析的谓词（未知键 {sorted(keys)}）: {obj!r}")


def property_from_dict(obj: dict, default_horizon: int = 6) -> Property:
    name = str(obj.get("name") or obj.get("kind") or "property")
    kind = str(obj.get("kind", "always")).lower()
    horizon = int(obj.get("horizon", default_horizon))
    description = str(obj.get("description", ""))
    if kind == "always":
        return Property.always(name, pred_from_obj(obj["body"]), horizon, description)
    if kind == "never":
        return Property.never(name, pred_from_obj(obj["body"]), horizon, description)
    if kind == "reach":
        return Property.reach(name, pred_from_obj(obj["body"]), horizon, description)
    if kind == "response":
        return Property.response(
            name,
            pred_from_obj(obj["trigger"]),
            pred_from_obj(obj["target"]),
            within=int(obj.get("within", 3)),
            horizon=horizon,
            description=description,
            sustain=int(obj.get("sustain", 1)),
        )
    raise ValueError(f"未知性质类型: {kind}（可用 always / never / response / reach）")
