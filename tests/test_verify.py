"""SMT 验证的测试：性质成立 / 反例存在 / 反例可信（回放复核）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from btfsv.fsm import build_machine
from btfsv.loader import load_json
from btfsv.properties import Atom, Event, Property
from btfsv.z3_verify import verify, verify_all

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def guard():
    return load_json(ROOT / "examples" / "guard.json")


@pytest.fixture(scope="module")
def buggy():
    return load_json(ROOT / "examples" / "buggy.json")


def test_guard_properties_hold(guard):
    m = build_machine(guard)
    results = verify_all(m, guard)
    by_name = {r.property.name: r for r in results}
    assert by_name["P1 反应性"].status == "holds_within_k"
    assert by_name["P2 无死锁"].status == "holds_within_k"
    assert by_name["P3 可达性"].status == "reachable"


def test_buggy_tree_violates_reaction(buggy):
    m = build_machine(buggy)
    results = verify_all(m, buggy)
    by_name = {r.property.name: r for r in results}
    p1 = by_name["P1 反应性"]
    assert p1.status == "violated"
    # 反例必须经过具体语义回放确认
    assert p1.confirmed
    assert p1.bad_index is not None
    # 反例起点：敌人可见，但此后 2 个 tick 内没有 flee
    assert p1.steps[p1.bad_index].env["see_enemy"] is True
    window = p1.steps[p1.bad_index : p1.bad_index + 3]
    assert all("flee" not in s.events for s in window)


def test_buggy_tree_still_satisfies_exclusion(buggy):
    m = build_machine(buggy)
    results = {r.property.name: r for r in verify_all(m, buggy)}
    assert results["P4 互斥"].status == "holds_within_k"


def test_safety_property_is_detected_as_violated(guard):
    """never(see_enemy) 显然不成立；用它检验"安全性质"编码方向没有写反。"""
    m = build_machine(guard)
    p = Property.never("敌人永不出现", Atom("see_enemy"), horizon=6)
    r = verify(m, p, tree=guard)
    assert r.status == "violated"
    assert r.confirmed
    assert r.steps[r.bad_index].env["see_enemy"] is True


def test_response_with_zero_window(guard):
    """within=0 时退化为 G(φ → ψ)，即要求同一 tick 内完成。"""
    m = build_machine(guard)
    p = Property.response("瞬时反应", Atom("see_enemy"), Event("flee"), within=0, horizon=6)
    r = verify(m, p, tree=guard)
    assert r.status in ("violated", "holds_within_k")
    if r.status == "violated":
        assert r.confirmed


def test_horizon_override(guard):
    m = build_machine(guard)
    p = guard.properties[0]
    r = verify(m, p, tree=guard, horizon=3)
    assert r.horizon == 3
    assert r.stats["horizon"] == 3


def test_encoding_budget_skips(guard):
    m = build_machine(guard)
    p = guard.properties[0]
    r = verify(m, p, tree=guard, encoding_budget=1)
    assert r.status == "skipped"
