"""JSON 解析与静态检查的测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from btfsv.loader import load_dict, load_json

ROOT = Path(__file__).resolve().parents[1]


def test_load_guard_example():
    tree = load_json(ROOT / "examples" / "guard.json")
    assert tree.atoms == ("low_hp", "see_enemy")
    assert tree.actions == ("flee", "heal", "patrol")
    assert len(tree.properties) == 3
    assert tree.properties[0].ltl.startswith("G (")


def test_load_buggy_example():
    tree = load_json(ROOT / "examples" / "buggy.json")
    # inverter 包住的 condition 仍然算原子命题
    assert "low_hp" in tree.atoms
    assert tree.find("inv_hp").kind == "inverter"


def test_auto_node_ids():
    tree = load_dict(
        {
            "root": {
                "type": "sequence",
                "children": [
                    {"type": "condition", "atom": "p"},
                    {"type": "action", "name": "a"},
                ],
            }
        }
    )
    ids = [n.id for n in tree.nodes()]
    assert ids == ["root", "root_0", "root_1"]


def test_unknown_node_type():
    with pytest.raises(ValueError, match="未知节点类型"):
        load_dict({"root": {"type": "while", "children": []}})


def test_duplicate_ids():
    with pytest.raises(ValueError, match="id 重复"):
        load_dict(
            {
                "root": {
                    "type": "sequence",
                    "id": "x",
                    "children": [
                        {"type": "condition", "id": "x", "atom": "p"},
                        {"type": "action", "id": "y", "name": "a"},
                    ],
                }
            }
        )


def test_property_references_unknown_atom():
    with pytest.raises(ValueError, match="不存在的原子命题"):
        load_dict(
            {
                "root": {"type": "condition", "atom": "p"},
                "properties": [{"kind": "always", "body": {"atom": "q"}}],
            }
        )


def test_property_references_unknown_action():
    with pytest.raises(ValueError, match="不存在的动作"):
        load_dict(
            {
                "root": {
                    "type": "sequence",
                    "children": [
                        {"type": "condition", "atom": "p"},
                        {"type": "action", "name": "a"},
                    ],
                },
                "properties": [{"kind": "response", "trigger": "p", "target": {"event": "b"}}],
            }
        )


def test_parallel_k_out_of_range():
    with pytest.raises(ValueError, match="k 必须在"):
        load_dict(
            {
                "root": {
                    "type": "parallel",
                    "k": 3,
                    "children": [
                        {"type": "condition", "atom": "p"},
                        {"type": "action", "name": "a"},
                    ],
                }
            }
        )


def test_tree_requires_condition():
    with pytest.raises(ValueError, match="没有任何 condition"):
        load_dict({"root": {"type": "action", "name": "a"}})


def test_roundtrip_to_dict():
    tree = load_json(ROOT / "examples" / "guard.json")
    again = load_dict(tree.to_dict())
    assert again.atoms == tree.atoms
    assert [p.name for p in again.properties] == [p.name for p in tree.properties]


def test_bad_json():
    bad = ROOT / "build" / "_bad_test.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{ not json", encoding="utf-8")
    try:
        with pytest.raises(ValueError, match="不是合法 JSON"):
            load_json(bad)
    finally:
        bad.unlink(missing_ok=True)
