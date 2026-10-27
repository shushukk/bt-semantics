"""命令行入口：``python -m btfsv <command>``。"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from pathlib import Path

from . import sos
from .fsm import Machine, StateExplosion, build_machine
from .loader import load_json
from .status import Status
from .tree import Tree
from .z3_verify import verify, verify_all

W = 78


def _hr(ch: str = "─") -> str:
    return ch * W


def _dw(text: str) -> int:
    """显示宽度：CJK 字符按 2 列计算，保证终端表格对齐。"""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in str(text))


def _pad(text: str, width: int) -> str:
    return str(text) + " " * max(0, width - _dw(text))


def _fmt_env(env: dict[str, bool], atoms: list[str]) -> str:
    return " ".join(f"{a}={'T' if env.get(a, False) else 'F'}" for a in atoms)


def print_steps(steps, atoms, bad: int | None = None, mark_window: int = 0) -> None:
    labels = [_fmt_env(s.env, atoms) for s in steps]
    ew = max([_dw(x) for x in labels] + [_dw("环境")])
    print(f"{_pad('tick', 4)} | {_pad('环境', ew)} | {_pad('根状态', 6)} | 事件")
    print(_hr("-"))
    for s, lab in zip(steps, labels):
        flag = " ← 反例窗口" if bad is not None and bad <= s.index <= bad + mark_window else ""
        ev = ",".join(s.events) if s.events else "-"
        print(f"{_pad(str(s.index), 4)} | {_pad(lab, ew)} | {_pad(s.status.zh, 6)} | {ev}{flag}")


def cmd_show(args) -> int:
    tree = load_json(args.tree)
    print(f"行为树: {tree.name}")
    print(_hr("="))
    print(tree)
    print(_hr("-"))
    print(f"原子命题 ({len(tree.atoms)}): {', '.join(tree.atoms)}   →  环境组合数 2^{len(tree.atoms)} = {2 ** len(tree.atoms)}")
    print(f"动作     ({len(tree.actions)}): {', '.join(tree.actions)}")
    for p in tree.properties:
        print(f"性质 {p.name}: {p.ltl}   (horizon={p.horizon})")
    return 0


def cmd_rules(args) -> int:
    fmt = args.format
    text = sos.render_text() if fmt == "text" else sos.render_markdown() if fmt == "markdown" else sos.render_latex()
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"已写入 {args.out}")
    else:
        print(text)
    if args.analogy:
        print("\n属性文法类比:\n")
        rows = sos.grammar_analogy()
        w1 = max(_dw(r[0]) for r in rows)
        w2 = max(_dw(r[1]) for r in rows)
        print(f"{_pad('属性文法', w1)}  {_pad('行为树', w2)}  说明")
        print(_hr("-"))
        for a, b, c in rows:
            print(f"{_pad(a, w1)}  {_pad(b, w2)}  {c}")
    return 0


def cmd_run(args) -> int:
    tree = load_json(args.tree)
    atoms = list(tree.atoms)

    script: list[dict[str, bool]] = []
    for spec in args.env or []:
        env = {a: False for a in atoms}
        for part in spec.split(","):
            part = part.strip()
            if not part:
                continue
            if "=" in part:
                k, v = part.split("=", 1)
                k = k.strip()
                if k not in env:
                    print(f"[错误] 未知原子命题 {k!r}（可用: {', '.join(atoms)}）", file=sys.stderr)
                    return 2
                env[k] = str(v).strip().lower() in ("1", "true", "t", "yes", "y", "真")
            else:
                if part not in env:
                    print(f"[错误] 未知原子命题 {part!r}", file=sys.stderr)
                    return 2
                env[part] = True
        script.append(env)
    if not script:
        script = [{a: False for a in atoms}]
    while len(script) < args.ticks:
        script.append(dict(script[-1]))

    machine = build_machine(tree)
    steps = machine.simulate(tree, script[: args.ticks])
    print(f"模拟 {tree.name}（{args.ticks} tick）")
    print(_hr("="))
    print_steps(steps, atoms)
    if args.memory:
        print()
        print("节点记忆（书签）在每拍结束时的值：")
        for step in steps:
            desc = machine.describe_state(tree, step.state) if step.state is not None else "?"
            print(f"  t={step.index}  {desc}")
    triggered = [s for step in steps for s in step.events]
    print(_hr("-"))
    print("触发序列: " + (" → ".join(triggered) if triggered else "（无）"))
    return 0


def cmd_explore(args) -> int:
    tree = load_json(args.tree)
    try:
        machine = build_machine(tree, state_limit=args.state_limit, atom_limit=args.atom_limit)
    except StateExplosion as exc:
        print(f"[状态爆炸] {exc}")
        return 3
    s = machine.summary()
    print(f"状态空间（{tree.name}）")
    print(_hr("="))
    print(f"可达状态   : {s['states']:,}")
    print(f"转移边     : {s['transitions']:,}  (状态 × 2^{s['atoms']} 个环境)")
    print(f"动作事件   : {', '.join(machine.actions)}")
    print(_hr("-"))
    print("前若干状态的记忆快照（μ，只显示非零分量）:")
    for i in range(min(args.show, machine.num_states)):
        print(f"  s{i:<3} {machine.describe_state(tree, i)}")
    if args.dot:
        Path(args.dot).write_text(machine.to_dot(), encoding="utf-8")
        print(f"\n已导出 DOT: {args.dot}（可用 `dot -Tsvg {args.dot} -o fsm.svg` 渲染）")
    return 0


def cmd_verify(args) -> int:
    tree = load_json(args.tree)
    try:
        machine = build_machine(tree, state_limit=args.state_limit)
    except StateExplosion as exc:
        print(f"[状态爆炸] {exc}")
        return 3

    if not tree.properties:
        print("[错误] 该树没有声明 properties，无法验证", file=sys.stderr)
        return 2

    results = verify_all(machine, tree, horizon=args.horizon, timeout_ms=int(args.timeout * 1000))
    bad = 0
    print(f"有界模型检测（{tree.name}）")
    print(_hr("="))
    print(f"FSM: {machine.num_states:,} 状态 / {machine.num_transitions:,} 转移    z3 {_z3_version()}")
    for r in results:
        print(_hr("-"))
        p = r.property
        print(f"[{p.name}]")
        print(f"  LTL    : {p.ltl}")
        print(f"  结论   : {r.status_zh}   ({r.status})")
        st = r.stats
        if "constraints" in st:
            print(
                f"  规模   : horizon={st['horizon']}  约束={st['constraints']:,}  "
                f"编码项≈{st['encoding_terms']:,}  编码={st['encode_seconds']}s  求解={st['solve_seconds']}s"
            )
        if r.message:
            print(f"  说明   : {r.message}")
        if r.steps:
            print()
            print_steps(r.steps, list(tree.atoms), bad=r.bad_index, mark_window=p.within)
            if r.bad_index is not None:
                print(_hr("-"))
                if p.kind == "response":
                    print(
                        f"  反例起点: tick {r.bad_index} —— 此时 {p.trigger} 成立，"
                        f"但此后 {p.within} 个 tick 内 {p.target} 始终未发生"
                    )
                else:
                    print(f"  反例/见证位置: tick {r.bad_index}")
            print(f"  回放复核: {'通过（具体语义确认该轨迹）' if r.confirmed else '未通过（编码可能有问题）'}")
        if r.status in ("violated", "skipped", "unknown"):
            bad += 1
    print(_hr("="))
    print(f"共 {len(results)} 条性质，其中 {bad} 条未通过/未完成")
    return 1 if (bad and args.fail_on_violation) else 0


def _z3_version() -> str:
    try:
        import z3

        return z3.get_version_string()
    except Exception:  # pragma: no cover
        return "n/a"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="btfsv",
        description="行为树形式语义建模与 SMT 验证 Demo（课程报告配套工具）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  python -m btfsv show     examples/guard.json
  python -m btfsv run      examples/guard.json --env "see_enemy=true" --ticks 6
  python -m btfsv explore  examples/buggy.json --dot build/buggy.dot
  python -m btfsv verify   examples/buggy.json
  python -m btfsv rules    --format latex --out build/rules.tex
""",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("show", help="打印树结构与元信息")
    s.add_argument("tree")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("rules", help="输出 SOS 推理规则")
    s.add_argument("--format", choices=["text", "markdown", "latex"], default="text")
    s.add_argument("--out", help="写入文件")
    s.add_argument("--analogy", action="store_true", help="附带属性文法类比表")
    s.set_defaults(func=cmd_rules)

    s = sub.add_parser("run", help="按环境脚本模拟执行")
    s.add_argument("tree")
    s.add_argument("--env", action="append", help='每个 tick 的环境，如 "see_enemy=true,low_hp=false"；可重复')
    s.add_argument("--ticks", type=int, default=6)
    s.add_argument("--memory", action="store_true", help="额外打印每拍的节点记忆（书签）")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("explore", help="显式状态空间探索（抽取 FSM）")
    s.add_argument("tree")
    s.add_argument("--dot", help="导出 DOT 文件")
    s.add_argument("--show", type=int, default=8, help="打印前 N 个状态的记忆快照")
    s.add_argument("--state-limit", type=int, default=200_000)
    s.add_argument("--atom-limit", type=int, default=12)
    s.set_defaults(func=cmd_explore)

    s = sub.add_parser("verify", help="有界模型检测（z3）")
    s.add_argument("tree")
    s.add_argument("--horizon", type=int, default=None, help="覆盖性质中声明的 horizon")
    s.add_argument("--timeout", type=float, default=60.0, help="单条性质求解超时（秒）")
    s.add_argument("--state-limit", type=int, default=200_000)
    s.add_argument("--fail-on-violation", action="store_true", help="发现反例时以退出码 1 结束（便于 CI）")
    s.set_defaults(func=cmd_verify)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except FileNotFoundError as exc:
        print(f"[错误] 找不到文件: {exc.filename}", file=sys.stderr)
        return 2
    except (ValueError, KeyError) as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2
