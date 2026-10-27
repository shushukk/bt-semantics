"""状态空间抽取的测试：确定性、δ 一致性、状态爆炸保护。"""

from __future__ import annotations

from pathlib import Path

import pytest

from btfsv.fsm import StateExplosion, build_machine, enumerate_envs
from btfsv.loader import load_json

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def guard():
    return load_json(ROOT / "examples" / "guard.json")


def test_environment_enumeration():
    envs = enumerate_envs(("a", "b"))
    assert len(envs) == 4
    assert envs[0] == {"a": False, "b": False}
    assert envs[-1] == {"a": True, "b": True}


def test_machine_shape(guard):
    m = build_machine(guard)
    assert m.num_states == len(m.states)
    assert m.num_envs == 2 ** len(guard.atoms)
    # 每个状态在每个环境输入下都恰好有一条出边
    assert m.num_transitions == m.num_states * m.num_envs
    assert m.state_index[m.states[0]] == m.start


def test_determinism(guard):
    a = build_machine(guard)
    b = build_machine(guard)
    assert a.states == b.states
    assert a.transitions == b.transitions


def test_replay_matches_transition_relation(guard):
    """回放轨迹上的状态编号必须与 δ 一致 —— 检验解释器和 FSM 是同一个语义。"""
    m = build_machine(guard)
    env_indices = [m.envs.index({"low_hp": False, "see_enemy": True})] * 6
    steps = m.replay(guard, env_indices)

    cur = m.start
    for t, step in enumerate(steps):
        assert step.state is not None
        tr = [x for x in m.outgoing(cur) if x.env == env_indices[t]][0]
        assert tr.dst == step.state, f"tick {t} 的 δ 与回放不一致"
        assert tr.events == step.events, f"tick {t} 的事件与回放不一致"
        cur = step.state


def test_dot_export(guard):
    m = build_machine(guard)
    dot = m.to_dot()
    assert dot.startswith("digraph bt {")
    assert dot.rstrip().endswith("}")


def test_state_explosion_budget(guard):
    with pytest.raises(StateExplosion):
        build_machine(guard, state_limit=2)


def test_unsat_is_bounded_not_proof(guard):
    """机器本身的规模应当是有限的（这正是不变量带来的好处）。"""
    m = build_machine(guard, state_limit=10_000)
    assert m.num_states < 10_000
