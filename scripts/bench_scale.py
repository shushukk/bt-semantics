#!/usr/bin/env python3
"""规模基准：状态空间随行为树规模的增长，以及 z3 求解耗时。

用法::

    python scripts/bench_scale.py                      # 默认扫描并输出到 build/
    python scripts/bench_scale.py --widths 1,2,3,4,5   # 指定分支数
    python scripts/bench_scale.py --no-z3              # 只测状态空间

产出：

* ``build/bench.csv``  —— 原始数据（可直接喂给 matplotlib / Excel）
* ``build/bench.md``   —— Markdown 表格（贴进报告）
* ``build/bench.html`` —— 自带 SVG 折线图的单页报告

实验设计
--------
被测树形（宽度 w、动作时长 d_i，每个分支有**独立**的原子命题 p_i）：

    Parallel(k=w)
      ├── Sequence[Condition(p_0), Action(a_0, d_0)]
      ├── ...
      └── Sequence[Condition(p_{w-1}), Action(a_{w-1}, d_{w-1})]

三个规模维度同时增长，正是工程上的真实困境：

1. **状态数** |S| ≈ Π_i d_i —— 并行分支的动作进度可以任意组合；
2. **环境组合** 2^w —— 每个分支的守卫条件各占一个原子命题；
3. **转移边** |δ| = |S| × 2^w —— 两者相乘，最先撞墙的就是它。

（对照组：若把原子命题数固定为 2，各分支进度会被环境"耦合"住，状态数会饱和 ——
这个反例本身也值得写进报告：爆炸的根源是"并发的独立性"，而不是"节点个数"。）
"""

from __future__ import annotations

import argparse
import csv
import html
import math
import sys
import time

try:
    import resource
except ImportError:  # 非 POSIX 平台
    resource = None
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from btfsv.fsm import StateExplosion, build_machine  # noqa: E402
from btfsv.properties import Atom, Event, Implies, Property  # noqa: E402
from btfsv.tree import Action, Condition, Parallel, Sequence, Tree  # noqa: E402
from btfsv.z3_verify import verify  # noqa: E402

Z3_MAX_STATES = 3000        # 状态数超过它就不在单机上跑 BMC
Z3_MAX_TRANSITIONS = 12_000  # 转移边超过它同样跳过（编码规模 ≈ |δ|·k）
TRANSITION_LIMIT = 150_000   # 抽取阶段的转移边硬上限，防止 OOM


def peak_rss_mb() -> float:
    """进程峰值内存（MB）。注意是整个进程生命周期的最大值，跨实验只增不减。"""
    if resource is None:
        return float("nan")
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return raw / 1e6 if sys.platform == "darwin" else raw / 1e3  # macOS 字节 / Linux KB


def make_tree(width: int, base_duration: int = 2) -> Tree:
    """宽度为 w 的并行树：每个分支一个独立守卫 + 一个有时长的动作。"""
    atoms = tuple(f"p{i}" for i in range(width))
    branches = []
    actions = []
    for i in range(width):
        d = base_duration + (i % 3)
        actions.append(f"a{i}")
        branches.append(
            Sequence(
                f"seq{i}",
                [Condition(f"c{i}", atoms[i]), Action(f"act{i}", f"a{i}", d)],
            )
        )
    # 不变式：动作 a0 只可能在守卫 p0 为真的 tick 被触发
    # （结构上恒成立 → 期望 unsat，正好拿来压测"证明"这一侧的求解代价）
    prop = Property.always(
        "P1 守卫不变式",
        Implies(Event(actions[0]), Atom(atoms[0])),
        horizon=6,
    )
    return Tree(
        name=f"parallel-w{width}-d{base_duration}",
        root=Parallel("root", branches, k=width),
        atoms=atoms,
        actions=tuple(actions),
        properties=(prop,),
    )


def run_one(width: int, base_duration: int, state_limit: int, run_z3: bool) -> dict:
    tree = make_tree(width, base_duration)
    row: dict = {
        "series": base_duration,
        "rss_mb": None,
        "width": width,
        "durations": ",".join(str(base_duration + (i % 3)) for i in range(width)),
        "envs": 2 ** width,
        "states": None,
        "transitions": None,
        "build_s": None,
        "z3_status": "-",
        "z3_s": None,
        "note": "",
    }
    t0 = time.perf_counter()
    try:
        m = build_machine(
            tree, state_limit=state_limit, transition_limit=TRANSITION_LIMIT, atom_limit=12
        )
    except StateExplosion as exc:
        row["build_s"] = round(time.perf_counter() - t0, 2)
        row["rss_mb"] = round(peak_rss_mb(), 1)
        row["states"] = f">{exc.found:,}" if exc.what == "states" else "?"
        row["transitions"] = f">{exc.found:,}" if exc.what == "transitions" else "?"
        row["note"] = f"状态爆炸（{exc}）"
        return row
    row["build_s"] = round(time.perf_counter() - t0, 2)
    row["rss_mb"] = round(peak_rss_mb(), 1)
    row["states"] = m.num_states
    row["transitions"] = m.num_transitions

    if run_z3:
        if m.num_states > Z3_MAX_STATES or m.num_transitions > Z3_MAX_TRANSITIONS:
            row["z3_status"] = "skipped(规模)"
            row["note"] = (
                f"|S|={m.num_states:,} / |δ|={m.num_transitions:,} 超过单机 BMC 预算"
            )
        else:
            r = verify(m, tree.properties[0], tree=tree, encoding_budget=3_000_000, timeout_ms=60_000)
            row["z3_status"] = r.status
            row["z3_s"] = r.stats.get("solve_seconds")
            if not row["note"]:
                row["note"] = r.message[:40]
    return row


def _fmt(v) -> str:
    return v if isinstance(v, str) else f"{v:,}"


def to_markdown(rows: list[dict]) -> str:
    lines = [
        "| 分支数 w | 动作时长 | 环境数 2^w | 状态数 \\|S\\| | 转移边 \\|δ\\| | 抽取耗时(s) | 峰值内存(MB) | BMC 结论 | 求解(s) |",
        "|---:|---|---:|---:|---:|---:|---:|---|---:|",
    ]
    for r in rows:
        z3s = f"{r['z3_s']:.3f}" if r["z3_s"] is not None else "-"
        lines.append(
            f"| {r['width']} | {r['durations']} | {r['envs']:,} | {_fmt(r['states'])} | "
            f"{_fmt(r['transitions'])} | {r['build_s']:.2f} | {r['rss_mb']:.1f} | {r['z3_status']} | {z3s} |"
        )
    return "\n".join(lines)


def to_html(rows: list[dict], state_limit: int, transition_limit: int) -> str:
    """自包含 HTML 报告：对数纵轴的 SVG 折线图 + 数据表。"""
    pts = [(r["width"], r["states"]) for r in rows if isinstance(r["states"], int)]
    W, H = 740, 330
    pad_l, pad_b, pad_t, pad_r = 68, 46, 28, 26
    max_states = max([p[1] for p in pts], default=10)
    dec = max(1, math.ceil(math.log10(max_states)))
    widths = sorted({r["width"] for r in rows})
    wmin, wmax = widths[0], widths[-1]

    def sx(w: int) -> float:
        return pad_l + (w - wmin) / max(1, wmax - wmin) * (W - pad_l - pad_r)

    def sy(n: float) -> float:
        return H - pad_b - (math.log10(max(n, 1)) / dec) * (H - pad_t - pad_b)

    palette = ["#2f6feb", "#e8710a", "#1a9c62", "#9147c9"]
    series = sorted({r["series"] for r in rows if isinstance(r["states"], int)})
    lines: list[str] = []
    legend: list[str] = []
    for si, base in enumerate(series):
        color = palette[si % len(palette)]
        pts_s = [(r["width"], r["states"]) for r in rows if r["series"] == base and isinstance(r["states"], int)]
        if not pts_s:
            continue
        lines.append(
            f'<polyline points="{" ".join(f"{sx(w):.1f},{sy(n):.1f}" for w, n in pts_s)}" '
            f'fill="none" stroke="{color}" stroke-width="2"/>'
        )
        lines.append(
            "".join(
                f'<circle cx="{sx(w):.1f}" cy="{sy(n):.1f}" r="3.5" fill="{color}"/>'
                f'<text x="{sx(w):.1f}" y="{sy(n) - 9:.1f}" font-size="10" fill="#555" '
                f'text-anchor="middle">{n:,}</text>'
                for w, n in pts_s
            )
        )
        legend.append(
            f'<g transform="translate({W - 200},{16 + si * 18})">'
            f'<rect width="14" height="3" y="6" fill="{color}"/>'
            f'<text x="20" y="12" font-size="11" fill="#444">动作时长基准 d={base}</text></g>'
        )
    poly = "".join(lines)
    legend_svg = "".join(legend)
    grid = "".join(
        f'<line x1="{pad_l}" y1="{H - pad_b - k / dec * (H - pad_t - pad_b):.1f}" '
        f'x2="{W - pad_r}" y2="{H - pad_b - k / dec * (H - pad_t - pad_b):.1f}" stroke="#e8e8ea"/>'
        f'<text x="{pad_l - 8}" y="{H - pad_b - k / dec * (H - pad_t - pad_b) + 4:.1f}" '
        f'font-size="10" fill="#666" text-anchor="end">1e{k}</text>'
        for k in range(dec + 1)
    )
    xlabels = "".join(
        f'<text x="{sx(w):.1f}" y="{H - pad_b + 18}" font-size="11" fill="#555" '
        f'text-anchor="middle">w={w}</text>'
        for w in widths
    )

    trs_parts = []
    for r in rows:
        z3s = f"{r['z3_s']:.3f}" if r["z3_s"] is not None else "-"
        trs_parts.append(
            "<tr>"
            f"<td>{r['width']}</td><td>{r['durations']}</td>"
            f"<td style='text-align:right'>{r['envs']:,}</td>"
            f"<td style='text-align:right'>{_fmt(r['states'])}</td>"
            f"<td style='text-align:right'>{_fmt(r['transitions'])}</td>"
            f"<td style='text-align:right'>{r['build_s']:.2f}</td>"
            f"<td style='text-align:right'>{r['rss_mb']:.1f}</td>"
            f"<td>{r['z3_status']}</td>"
            f"<td style='text-align:right'>{z3s}</td>"
            f"<td>{html.escape(str(r['note']))}</td>"
            "</tr>"
        )
    trs = "".join(trs_parts)

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>行为树状态空间规模基准</title>
<style>
 body{{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;margin:0;padding:32px;background:#fbfbfc;color:#1c1e21}}
 h1{{font-size:20px;margin:0 0 6px}} h2{{font-size:15px;margin:22px 0 10px}}
 p.sub{{color:#666;font-size:13px;margin:0 0 22px;line-height:1.7}}
 .card{{background:#fff;border:1px solid #e6e8eb;border-radius:12px;padding:18px;margin-bottom:20px}}
 table{{border-collapse:collapse;width:100%;font-size:13px}}
 th,td{{border-bottom:1px solid #eee;padding:7px 10px;text-align:left}}
 th{{background:#f6f7f9;font-weight:600;color:#333}}
 code{{background:#f2f3f5;padding:1px 5px;border-radius:4px;font-size:12px}}
</style></head><body>
<h1>行为树状态空间规模基准</h1>
<p class="sub">树形：<code>Parallel(k=w)</code> 下挂 w 个 <code>Sequence[Condition(p_i), Action(d_i)]</code>。<br>
三个维度同时增长：状态数 ≈ Π d_i、环境数 2^w、转移边 = 状态数 × 环境数。<br>
状态上限 {state_limit:,}，转移边上限 {transition_limit:,}。</p>
<div class="card">
<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}">
 {grid}
 <line x1="{pad_l}" y1="{H - pad_b}" x2="{W - pad_r}" y2="{H - pad_b}" stroke="#c9cbd0"/>
 {poly}{legend_svg}{xlabels}
 <text x="{pad_l}" y="{pad_t - 8}" font-size="11" fill="#666">可达状态数 |S|（对数轴）</text>
</svg>
</div>
<h2>实测数据</h2>
<div class="card"><table>
<thead><tr><th>w</th><th>动作时长</th><th>环境数</th><th>状态数</th><th>转移边</th>
<th>抽取(s)</th><th>峰值内存(MB)</th><th>BMC</th><th>求解(s)</th><th>备注</th></tr></thead>
<tbody>{trs}</tbody></table></div>
<p class="sub">复现：<code>python scripts/bench_scale.py</code></p>
</body></html>"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="行为树状态空间规模基准")
    ap.add_argument("--widths", default="1,2,3,4,5,6", help="分支数扫描列表")
    ap.add_argument("--durations", default="2,3,4", help="动作基准时长列表")
    ap.add_argument("--state-limit", type=int, default=100_000)
    ap.add_argument("--no-z3", action="store_true", help="只测状态空间，不跑 BMC")
    ap.add_argument("--out-dir", default=str(ROOT / "build"))
    args = ap.parse_args(argv)

    widths = [int(x) for x in args.widths.split(",") if x.strip()]
    durations = [int(x) for x in args.durations.split(",") if x.strip()]

    rows: list[dict] = []
    print(
        f"{'w':>2} {'durations':<14}{'envs':>7}{'states':>11}{'trans':>11}"
        f"{'build(s)':>10}{'rss(MB)':>9}  {'z3':<16}{'solve(s)':>9}"
    )
    print("-" * 86)
    for d in durations:
        for w in widths:
            row = run_one(w, d, args.state_limit, run_z3=not args.no_z3)
            rows.append(row)
            z3s = "" if row["z3_s"] is None else f"{row['z3_s']:.3f}"
            print(
                f"{w:>2} {row['durations']:<14}{row['envs']:>7}{_fmt(row['states']):>11}"
                f"{_fmt(row['transitions']):>11}{row['build_s']:>10.2f}"
                f"{row['rss_mb']:>9.1f}  {row['z3_status']:<16}{z3s:>9}",
                flush=True,
            )
            if row["note"].startswith("状态爆炸"):
                print("   ↑ 撞到上限，本组扫描提前结束")
                break

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "bench.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    (out / "bench.md").write_text(
        "# 规模基准（自动生成）\n\n"
        f"状态上限 {args.state_limit:,}，转移边上限 {TRANSITION_LIMIT:,}。\n\n"
        + to_markdown(rows)
        + "\n",
        encoding="utf-8",
    )
    (out / "bench.html").write_text(
        to_html(rows, args.state_limit, TRANSITION_LIMIT), encoding="utf-8"
    )
    print("-" * 86)
    print(f"已输出: {out / 'bench.csv'} / {out / 'bench.md'} / {out / 'bench.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
