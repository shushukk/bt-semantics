"""JSON → 行为树，并做静态检查。

JSON 模式（DSL 的语法定义）::

    {
      "name": "守卫撤退",
      "root": <node>,
      "properties": [ <property>, ... ]
    }

    <node> ::= {"type": "condition", "id": ?, "atom": str}
             | {"type": "action",    "id": ?, "name": str, "duration": int = 1}
             | {"type": "sequence",  "id": ?, "children": [<node>...]}
             | {"type": "selector",  "id": ?, "children": [<node>...]}
             | {"type": "parallel",  "id": ?, "k": int, "children": [<node>...]}
             | {"type": "inverter",  "id": ?, "child": <node>}

静态检查（相当于编译器的"语义分析"阶段）：

1. 节点类型合法性、必填字段、``parallel`` 的 ``k`` 取值范围；
2. 节点 id 唯一（缺省时按路径自动生成，如 ``root/0/1``）；
3. 性质中引用的原子命题 / 动作名必须在树里出现过。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .properties import Property, property_from_dict
from .tree import Action, Condition, Inverter, Node, Parallel, Selector, Sequence, Tree

_NODE_TYPES = {"condition", "action", "sequence", "selector", "parallel", "inverter"}


def _auto_id(path: str) -> str:
    return path.replace("/", "_") or "root"


def _build_node(obj: Any, path: str = "root") -> Node:
    if not isinstance(obj, dict):
        raise ValueError(f"[{path}] 节点必须是对象，实际是 {type(obj).__name__}")
    ntype = str(obj.get("type", "")).lower()
    if ntype not in _NODE_TYPES:
        raise ValueError(f"[{path}] 未知节点类型 {ntype!r}，可用: {sorted(_NODE_TYPES)}")
    node_id = str(obj.get("id") or _auto_id(path))

    if ntype == "condition":
        if "atom" not in obj:
            raise ValueError(f"[{path}] condition 节点缺少 atom 字段")
        return Condition(node_id, str(obj["atom"]))

    if ntype == "action":
        if "name" not in obj:
            raise ValueError(f"[{path}] action 节点缺少 name 字段")
        return Action(node_id, str(obj["name"]), int(obj.get("duration", 1)))

    if ntype == "inverter":
        if "child" not in obj:
            raise ValueError(f"[{path}] inverter 节点缺少 child 字段")
        return Inverter(node_id, _build_node(obj["child"], f"{path}/0"))

    raw_children = obj.get("children")
    if not isinstance(raw_children, list) or not raw_children:
        raise ValueError(f"[{path}] {ntype} 节点需要非空的 children 数组")
    children = [_build_node(c, f"{path}/{i}") for i, c in enumerate(raw_children)]

    if ntype == "sequence":
        return Sequence(node_id, children)
    if ntype == "selector":
        return Selector(node_id, children)
    k = obj.get("k")
    return Parallel(node_id, children, None if k is None else int(k))


def load_dict(data: dict, *, strict: bool = True) -> Tree:
    """从已解析的 JSON 字典构造行为树。"""
    if not isinstance(data, dict):
        raise ValueError("根对象必须是 JSON 对象")
    if "root" not in data:
        raise ValueError('缺少 "root" 字段')

    root = _build_node(data["root"])

    # id 唯一性检查
    seen: dict[str, int] = {}
    for n in root.walk():
        seen[n.id] = seen.get(n.id, 0) + 1
    dup = [k for k, v in seen.items() if v > 1]
    if dup:
        raise ValueError(f"节点 id 重复: {dup}")

    atoms = sorted({n.atom for n in root.walk() if isinstance(n, Condition)})
    actions = sorted({n.name for n in root.walk() if isinstance(n, Action)})
    if not atoms:
        raise ValueError("树中没有任何 condition 节点：环境原子命题为空，性质将无法表达")

    raw_props = data.get("properties") or []
    if not isinstance(raw_props, list):
        raise ValueError('"properties" 必须是数组')
    props: list[Property] = [
        property_from_dict(p, default_horizon=int(data.get("horizon", 6))) for p in raw_props
    ]

    if strict:
        for p in props:
            unknown_atoms = p.atoms() - set(atoms)
            unknown_actions = p.actions() - set(actions)
            if unknown_atoms:
                raise ValueError(f"性质 {p.name!r} 引用了树中不存在的原子命题: {sorted(unknown_atoms)}")
            if unknown_actions:
                raise ValueError(f"性质 {p.name!r} 引用了树中不存在的动作: {sorted(unknown_actions)}")

    return Tree(
        name=str(data.get("name") or "behavior-tree"),
        root=root,
        atoms=tuple(atoms),
        actions=tuple(actions),
        properties=tuple(props),
        source=data,
    )


def load_json(path: str | Path, *, strict: bool = True) -> Tree:
    text = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:  # 给出更友好的报错
        raise ValueError(f"{path} 不是合法 JSON: {exc}") from exc
    return load_dict(data, strict=strict)


# 简写别名
load = load_json
