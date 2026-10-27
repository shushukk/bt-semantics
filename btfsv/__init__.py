"""btfsv —— Behavior Tree Formal Semantics & Verification.

行为树的形式语义建模与 SMT 验证工具链（课程报告 Demo 的可执行实现）。

流水线::

    JSON 树描述
      → loader     解析 + 静态检查（原子命题 / 动作 / 节点 id）
      → tree       Tick 解释器（SOS 规则的可执行版本）
      → fsm        显式状态空间探索，抽取出有限状态机
      → z3_verify  有界模型检测（BMC），找出性质反例并回放
"""

from .status import Status
from .tree import Tree, Node
from .loader import load, load_dict, load_json
from .fsm import Machine, Transition, Step, StateExplosion, build_machine
from .properties import Property, Pred, Atom, Event, Not, And, Or, Implies
from .z3_verify import verify, VerifyResult

__all__ = [
    "Status",
    "Tree",
    "Node",
    "load",
    "load_dict",
    "load_json",
    "Machine",
    "Transition",
    "Step",
    "StateExplosion",
    "build_machine",
    "Property",
    "Pred",
    "Atom",
    "Event",
    "Not",
    "And",
    "Or",
    "Implies",
    "verify",
    "VerifyResult",
]

__version__ = "0.1.0"
