"""节点返回状态。

三个取值直接对应论文 3.1 节的三值语义 :math:`\\mathbb{B}_3 = \\{F, R, S\\}`。
使用整数编码是为了让状态快照（state snapshot）可以直接做字典键。
"""

from __future__ import annotations

from enum import IntEnum


class Status(IntEnum):
    FAILURE = 0
    RUNNING = 1
    SUCCESS = 2

    @property
    def short(self) -> str:
        return {2: "S", 1: "R", 0: "F"}[int(self)]

    @property
    def zh(self) -> str:
        return {2: "成功", 1: "运行", 0: "失败"}[int(self)]

    @classmethod
    def parse(cls, text: str) -> "Status":
        table = {
            "s": cls.SUCCESS, "success": cls.SUCCESS, "成功": cls.SUCCESS,
            "f": cls.FAILURE, "failure": cls.FAILURE, "失败": cls.FAILURE,
            "r": cls.RUNNING, "running": cls.RUNNING, "运行": cls.RUNNING,
        }
        key = str(text).strip().lower()
        if key not in table:
            raise ValueError(f"无法识别的状态: {text!r}（可用 S/F/R）")
        return table[key]
