"""Tick 语义的单元测试：SOS 规则逐条对照。"""

from __future__ import annotations

import pytest

from btfsv.status import Status
from btfsv.tree import Action, Condition, Inverter, Parallel, Selector, Sequence, TickContext

F, R, S = Status.FAILURE, Status.RUNNING, Status.SUCCESS


def tick(node, **env):
    ctx = TickContext()
    st = node.tick({k: v for k, v in env.items()}, ctx)
    return st, ctx.events


# --------------------------------------------------------------------------- #
# 叶子
# --------------------------------------------------------------------------- #
def test_condition_reads_environment():
    c = Condition("c", "enemy")
    assert tick(c, enemy=True) == (S, [])
    assert tick(c, enemy=False) == (F, [])


def test_action_duration_and_event():
    a = Action("a", "flee", duration=3)
    assert tick(a) == (R, ["flee"])  # 开始的 tick 触发事件
    assert tick(a) == (R, [])        # 中间 tick 不重复触发
    assert tick(a) == (S, [])
    assert a.snapshot() == (0,)      # 终止即清零（不变量）
    assert tick(a) == (R, ["flee"])  # 再次进入 -> 重新触发


def test_action_duration_one():
    a = Action("a", "patrol", duration=1)
    assert tick(a) == (S, ["patrol"])


# --------------------------------------------------------------------------- #
# Sequence
# --------------------------------------------------------------------------- #
def test_sequence_all_success():
    seq = Sequence("s", [Condition("c1", "p"), Action("a1", "x", 1)])
    assert tick(seq, p=True) == (S, ["x"])


def test_sequence_fails_on_first_failure():
    seq = Sequence("s", [Condition("c1", "p"), Action("a1", "x", 1)])
    assert tick(seq, p=False) == (F, [])


def test_sequence_keeps_memory_while_running():
    seq = Sequence("s", [Action("a1", "x", 3), Action("a2", "y", 1)])
    assert tick(seq) == (R, ["x"])
    assert seq.snapshot()[0] == 0  # 停在第一个子节点上
    assert tick(seq) == (R, [])
    assert tick(seq) == (S, ["y"])
    assert seq.snapshot()[0] == 0  # 成功返回后指针归零


def test_sequence_resets_pointer_after_failure():
    seq = Sequence("s", [Action("a1", "x", 2), Condition("c", "z")])
    assert tick(seq, z=False) == (R, ["x"])
    st, _ = tick(seq, z=False)
    assert st == F
    assert seq.snapshot()[0] == 0


# --------------------------------------------------------------------------- #
# Selector
# --------------------------------------------------------------------------- #
def test_selector_priority():
    sel = Selector("q", [Condition("c1", "hi"), Condition("c2", "lo"), Action("a", "idle", 1)])
    assert tick(sel, hi=True, lo=True) == (S, [])       # 高优先级成功即返回
    assert tick(sel, hi=False, lo=True) == (S, [])
    assert tick(sel, hi=False, lo=False) == (S, ["idle"])


def test_selector_fails_when_all_fail():
    sel = Selector("q", [Condition("c1", "a"), Condition("c2", "b")])
    assert tick(sel, a=False, b=False) == (F, [])


def test_selector_sticks_to_running_branch():
    sel = Selector("q", [Sequence("s1", [Action("a1", "x", 3)]), Action("a2", "y", 1)])
    assert tick(sel) == (R, ["x"])
    # 即使后面出现更"紧急"的条件，选择节点也会继续跑当前分支（无抢占）
    assert tick(sel) == (R, [])
    assert tick(sel) == (S, [])


# --------------------------------------------------------------------------- #
# Parallel
# --------------------------------------------------------------------------- #
def test_parallel_threshold():
    par = Parallel("p", [Action("a", "x", 2), Action("b", "y", 3)], k=1)
    assert tick(par) == (R, ["x", "y"])
    st, ev = tick(par)
    assert st == S  # 第一个子节点成功，k=1 已满足
    assert ev == []


def test_parallel_failure_dominates():
    par = Parallel("p", [Condition("c", "no"), Condition("d", "no")], k=2)
    assert tick(par) == (F, [])


# --------------------------------------------------------------------------- #
# Inverter
# --------------------------------------------------------------------------- #
def test_inverter():
    inv = Inverter("i", Action("a", "x", 2))
    assert tick(inv) == (R, ["x"])
    assert tick(inv) == (F, [])


# --------------------------------------------------------------------------- #
# 记忆不变量
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("duration", [1, 2, 5])
def test_memory_reset_after_terminal(duration):
    """返回 S/F 之后，记忆必须回到初始值 —— FSM 状态空间有限的前提。"""
    seq = Sequence("s", [Action("a", "x", duration), Condition("c", "p")])
    st = None
    for _ in range(duration):
        st, _ = tick(seq, p=True)
    assert st == S
    assert seq.snapshot() == (0, ((0,), ()))
