"""SOS 推理规则的机器可读表示 + 渲染（text / markdown / latex）。

规则与 ``tree.py`` 的 ``tick`` 实现一一对应；写报告时可以直接

    python -m btfsv rules --format latex

把输出贴进论文，或 ``--format markdown`` 贴进 README。

记号
----
* :math:`\\sigma : \\mathrm{Atom} \\to \\{\\mathrm{tt}, \\mathrm{ff}\\}` —— 环境；
* :math:`\\mu` —— 记忆映射；
* :math:`\\downarrow st` —— 本 tick 终止并返回状态 :math:`st \\in \\{S, F\\}`；
* :math:`\\xrightarrow{\\;st\\;} \\mu'` —— 本 tick 返回 RUNNING，记忆更新为 :math:`\\mu'`；
* :math:`E` —— 本 tick 触发的事件集合。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence


@dataclass(frozen=True)
class Rule:
    code: str
    premises: tuple[str, ...]
    conclusion: str
    side: tuple[str, ...] = ()
    note: str = ""


def rules() -> list[Rule]:
    """返回全部 SOS 规则。"""
    return [
        Rule(
            code="C-COND",
            premises=("σ(φ) = tt",),
            conclusion="⟨cond φ, μ⟩ ↓ S, ∅",
            note="条件节点读取环境，不产生 RUNNING，也不改变记忆。",
        ),
        Rule(
            code="C-COND-F",
            premises=("σ(φ) = ff",),
            conclusion="⟨cond φ, μ⟩ ↓ F, ∅",
        ),
        Rule(
            code="A-RUN",
            premises=("c + 1 < d",),
            conclusion="⟨act a[c], μ⟩ → R, μ[c ↦ c+1], E0",
            side=("E0 = {start(a)} 若 c = 0，否则 E0 = ∅",),
            note="动作需要 d 个 tick 才完成；触发（start）发生在 c = 0 的那个 tick。",
        ),
        Rule(
            code="A-END",
            premises=("c + 1 = d",),
            conclusion="⟨act a[c], μ⟩ ↓ S, μ[c ↦ 0], E0",
            side=("E0 = {start(a)} 若 c = 0，否则 E0 = ∅",),
        ),
        Rule(
            code="S-SEQ-RUN",
            premises=("⟨c_i, μ⟩ → R, μ', E",),
            conclusion="⟨seq[c_1..c_n][i], μ⟩ → R, μ'[i ↦ i], E",
            side=("1 ≤ i ≤ n",),
            note="有子节点在运行：整棵树停在该子节点上（记忆 i 就是「续跑指针」）。",
        ),
        Rule(
            code="S-SEQ-F",
            premises=("⟨c_i, μ⟩ ↓ F, μ', E",),
            conclusion="⟨seq[c_1..c_n][i], μ⟩ ↓ F, μ'[i ↦ 0], E",
            note="任一子节点失败 → 顺序节点失败，指针归零。",
        ),
        Rule(
            code="S-SEQ-S",
            premises=("⟨c_i, μ⟩ ↓ S, μ', E", "⟨seq[c_1..c_n][i+1], μ'⟩ ↓ st, μ'', E'"),
            conclusion="⟨seq[c_1..c_n][i], μ⟩ ↓ st, μ''[i ↦ 0], E ∪ E'",
            side=("i + 1 ≤ n",),
            note="这里用「右递归」表述顺序推进，便于结构归纳。",
        ),
        Rule(
            code="S-SEL-RUN",
            premises=("⟨c_i, μ⟩ → R, μ', E",),
            conclusion="⟨sel[c_1..c_n][i], μ⟩ → R, μ'[i ↦ i], E",
            side=("1 ≤ i ≤ n",),
        ),
        Rule(
            code="S-SEL-S",
            premises=("⟨c_i, μ⟩ ↓ S, μ', E",),
            conclusion="⟨sel[c_1..c_n][i], μ⟩ ↓ S, μ'[i ↦ 0], E",
            note="任一子节点成功 → 选择节点成功（高优先级分支抢占）。",
        ),
        Rule(
            code="S-SEL-F",
            premises=("⟨c_i, μ⟩ ↓ F, μ', E", "⟨sel[c_1..c_n][i+1], μ'⟩ ↓ st, μ'', E'"),
            conclusion="⟨sel[c_1..c_n][i], μ⟩ ↓ st, μ''[i ↦ 0], E ∪ E'",
            side=("i + 1 ≤ n",),
        ),
        Rule(
            code="S-PAR-S",
            premises=("⟨c_j, μ⟩ → st_j, μ_j, E_j  (j = 1..n)", "|{ j | st_j = S }| ≥ k"),
            conclusion="⟨par^k[c_1..c_n], μ⟩ ↓ S, μ', ⋃_j E_j",
            note="并行节点每个 tick 推进所有子节点；达到 k 个成功即成功（无记忆）。",
        ),
        Rule(
            code="S-PAR-F",
            premises=("⟨c_j, μ⟩ → st_j, μ_j, E_j  (j = 1..n)", "|{ j | st_j = F }| > n - k"),
            conclusion="⟨par^k[c_1..c_n], μ⟩ ↓ F, μ', ⋃_j E_j",
            note="失败优先：剩余子节点已不可能凑够 k 个成功。",
        ),
        Rule(
            code="S-PAR-R",
            premises=("⟨c_j, μ⟩ → st_j, μ_j, E_j  (j = 1..n)", "|S| < k", "|F| ≤ n - k"),
            conclusion="⟨par^k[c_1..c_n], μ⟩ → R, μ', ⋃_j E_j",
        ),
        Rule(
            code="D-INV",
            premises=("⟨c, μ⟩ → st, μ', E",),
            conclusion="⟨inv c, μ⟩ → ¬st, μ', E",
            side=("¬S = F, ¬F = S, ¬R = R",),
        ),
    ]


def _dw(text: str) -> int:
    """显示宽度：CJK 字符算 2 列（终端里对齐用）。"""
    import unicodedata

    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in str(text))


def _width(lines: Sequence[str]) -> int:
    return max((_dw(s) for s in lines), default=0)


def _ljust(text: str, width: int) -> str:
    return text + " " * max(0, width - _dw(text))


def render_text(rs: Sequence[Rule] | None = None) -> str:
    rs = list(rs or rules())
    out: list[str] = []
    for r in rs:
        out.append(f"[{r.code}]")
        for s in r.side:
            out.append(f"    side: {s}")
        w = max(_width(r.premises), _dw(r.conclusion))
        for p in r.premises:
            out.append("    " + _ljust(p, w))
        out.append("    " + "─" * w)
        out.append("    " + r.conclusion)
        if r.note:
            out.append(f"    -- {r.note}")
        out.append("")
    return "\n".join(out)


def render_markdown(rs: Sequence[Rule] | None = None) -> str:
    rs = list(rs or rules())
    out: list[str] = ["# 行为树 Tick 语义的 SOS 推理规则", ""]
    for r in rs:
        out.append(f"### {r.code}")
        out.append("")
        out.append("```")
        w = max(_width(r.premises), _dw(r.conclusion))
        for p in r.premises:
            out.append(_ljust(p, w))
        out.append("─" * w)
        out.append(r.conclusion)
        out.append("```")
        if r.side:
            out.append("")
            for s in r.side:
                out.append(f"* 附加条件：{s}")
        if r.note:
            out.append("")
            out.append(f"> {r.note}")
        out.append("")
    return "\n".join(out)


_LATEX_CMD = {
    "⟨": r"\langle ", "⟩": r"\rangle ", "→": r"\rightarrow ", "↓": r"\downarrow ",
    "σ": r"\sigma ", "μ": r"\mu ", "φ": r"\varphi ", "¬": r"\neg ", "⋃": r"\bigcup ",
    "≤": r"\le ", "≥": r"\ge ", "∅": r"\emptyset ", "↦": r"\mapsto ", "∈": r"\in ",
    "=": "=", "{": r"\{", "}": r"\}",
}


def _to_latex(s: str) -> str:
    out = []
    for ch in s:
        out.append(_LATEX_CMD.get(ch, ch))
    text = "".join(out)
    text = text.replace("st_j", r"st_j").replace("c_1..c_n", r"c_1 \ldots c_n")
    return text


def render_latex(rs: Sequence[Rule] | None = None) -> str:
    rs = list(rs or rules())
    out: list[str] = ["% 需要 \\usepackage{amsmath,amssymb}", ""]
    for r in rs:
        premises = r" \qquad ".join(_to_latex(p) for p in r.premises)
        for s in r.side:
            out.append("% 附加条件: " + s)
        out.append(r"\begin{equation}")
        out.append(r"\frac{%s}{%s} \tag{%s}" % (premises, _to_latex(r.conclusion), r.code))
        out.append(r"\end{equation}")
        out.append("")
    return "\n".join(out)


def grammar_analogy() -> list[tuple[str, str, str]]:
    """属性文法 ↔ 行为树 Tick 的对应关系（报告 3.3 节的表格）。"""
    return [
        ("继承属性（inherited attribute）", "环境 σ 自上而下传递", "同一个 σ 传给所有子节点，子节点不能修改环境"),
        ("综合属性（synthesized attribute）", "子节点状态 st 自下而上返回", "父节点根据子节点的 st 决定自己的 st"),
        ("属性求值（attribute evaluation）", "一次 tick（自底向上 + 记忆转移）", "tick 是单遍求值；RUNNING 相当于求值被挂起"),
        ("带记忆的求值（惰性 / 增量）", "Sequence/Selector 的续跑指针 i", "下次从断点继续，避免重复求值已成功的子树"),
        ("循环 / 不动点（fixed point）", "反复 tick 直到根节点终止", "整棵树的执行 = 对 tick 求最小不动点"),
    ]
