"""报告里的小块纯格式化。

为什么单独一个模块：`run.py` 一旦被 import 就会把整套 pipeline 拉进 `sys.path`
（见 `tests/test_dependency_surface.py` 的元测试），所以**测试链不该 import 它**。
凡是需要被测试钉住的格式/判据，一律抽到这里来。
"""

import statistics

# 标签与取值**必须同源**。两处各写一份，就会漂移成「四个标签配三个值」。
LATENCY_LABELS = "均值 / 中位 / 最快 / 最慢"


def latency_summary(totals) -> str:
    """把一组耗时（秒）格式化成 `均值 / 中位 / 最快 / 最慢` 一行。

    🔴 四个标签必须配**四个值，而且印在同一行**。

    2026-09-20 实测到的缺陷：调用方把「均值」单独留在上一行的合计列，
    这一行仍然写四个标签却只给三个值 ⇒ 读者按左对齐会把 **39.1（真正的
    最慢）读成「最快」**，而「最慢」看着像缺失。
    该批合计列按原始表格复算 = 均值 23.5 / 中位 22.6 / 最快 20.0 / 最慢 39.1，
    与打印出来的三个数字逐一对上 —— 错的不是数字，是标签的位置。

    空输入返回 `""`（印不印由调用方决定）。
    """
    xs = [float(x) for x in totals]
    if not xs:
        return ""
    return (f"{statistics.mean(xs):.1f} / {statistics.median(xs):.1f} / "
            f"{min(xs):.1f} / {max(xs):.1f}")
