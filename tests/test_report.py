"""`src/report.py` 的契约测试。

存在理由：2026-09-20 复跑 50 批次时发现汇总行**标签与取值错位** ——
`均值 / 中位 / 最快 / 最慢` 四个标签，却只给三个值（均值被印在上一行）。
读者按左对齐会把 **39.1（真正的最慢）读成「最快」**。
数字没错，错的是标签的位置 —— 这类缺陷只能靠「标签数 == 取值数」钉死。
"""

import ast
from pathlib import Path

import pytest

from src import report

_ROOT = Path(__file__).resolve().parents[1]


# ── [1] 契约：标签数必须等于取值数 ──────────────────────────────────────

def test_labels_count_matches_values_count():
    """这是本模块存在的**唯一理由**：四个标签必须配四个值。"""
    labels = report.LATENCY_LABELS.split(" / ")
    values = report.latency_summary([20.0, 22.6, 23.5, 39.1]).split(" / ")
    assert len(labels) == 4, f"标签数量变了：{labels}"
    assert len(labels) == len(values), (
        f"标签 {len(labels)} 个 / 取值 {len(values)} 个 —— 又错位了")


def test_labels_are_the_documented_four():
    """钉住标签的**顺序与用词**，防止有人只改一边。"""
    assert report.LATENCY_LABELS.split(" / ") == ["均值", "中位", "最快", "最慢"]


# ── [2] 顺序：均值 / 中位 / 最快 / 最慢 ────────────────────────────────

def test_order_is_mean_median_min_max():
    """用**偏态**样本，让均值、中位、最快、最慢四个值互不相等。

    等距样本（如 1,2,3,4）会让均值==中位，掩盖顺序错误。
    """
    # mean=26.5  median=2.5  min=1.0  max=100.0 —— 四个值全不同
    assert report.latency_summary([1, 2, 3, 100]) == "26.5 / 2.5 / 1.0 / 100.0"


def test_third_value_is_fastest_not_slowest():
    """专门钉住「第三个值是**最快**」—— 正是当初被误读的那一格。"""
    parts = report.latency_summary([1, 2, 3, 100]).split(" / ")
    assert parts[2] == "1.0", f"第三个值应当是**最快**（1.0），实际 {parts[2]}"
    assert parts[3] == "100.0", f"第四个值应当是**最慢**（100.0），实际 {parts[3]}"


def test_symmetric_sample_gives_mean_equal_median():
    assert report.latency_summary([1, 2, 3, 4]) == "2.5 / 2.5 / 1.0 / 4.0"


def test_single_sample_collapses_all_four():
    assert report.latency_summary([7.5]) == "7.5 / 7.5 / 7.5 / 7.5"


# ── [3] 边界 ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("empty", [[], (), iter([])])
def test_empty_input_returns_empty_string(empty):
    """空输入返回 `""`，不抛异常 —— 印不印由调用方决定。"""
    assert report.latency_summary(empty) == ""


def test_accepts_ints_and_floats_and_strings():
    """取值来自日志/JSON，可能是 int、float 或数字字符串 —— 都要能吃。"""
    assert report.latency_summary([1, "2", 3.0]) == "2.0 / 2.0 / 1.0 / 3.0"


def test_all_values_formatted_to_one_decimal():
    """统一一位小数，避免 `1.0 / 1 / 1.00` 这种看着像不同精度来源的混排。"""
    out = report.latency_summary([1, 1, 1])
    assert out == "1.0 / 1.0 / 1.0 / 1.0"


# ── [4] 静态接线：标签与取值必须在同一个 print 里 ──────────────────────

def _prints_mentioning(text: str, needle: str) -> list[str]:
    """返回 `text` 里所有「实参中出现 `needle`」的 `print(...)` 调用的 AST dump。

    ⚠ 必须走 AST，不能搜文本 —— 注释里提到 `LATENCY_LABELS` 不算"印了它"，
      而本项目在注释里写反面教材是常态（搜文本会被自己的注释判红）。
    """
    hits = []
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "print"):
            continue
        seg = ast.dump(node)
        if needle in seg:
            hits.append(seg)
    return hits


def test_labels_and_values_share_one_print_call():
    """标签和取值必须落在**同一个 `print(...)` 调用**里。

    只测 `latency_summary()` 本身不够 —— 缺陷出在**调用方**：把均值挪到上一行，
    函数依旧全绿。所以必须静态检查**真正印这一行的那份源码**。

    ⚠ 2026-09-20（B5）这个 print 从 `run.py` 搬到了 `src/report.py`，检查目标
      随之改 —— 判据本身（标签与取值同处一个 print）没变。
      「找不到」**不能**算通过：那正是护栏失效的形态。
    """
    src = (_ROOT / "src" / "report.py").read_text(encoding="utf-8")
    hits = _prints_mentioning(src, "LATENCY_LABELS")

    assert len(hits) == 1, f"应当恰好有一处 print 用到 LATENCY_LABELS，实际 {len(hits)} 处"
    assert "latency_summary" in hits[0], (
        "标签所在的那个 print 里没有取值的调用 —— 标签和取值又分家了")


def test_run_py_no_longer_prints_the_latency_line_itself():
    """run.py 只能**调用**渲染层，不能自己留一份打印逻辑。

    这条是给 B5 的搬迁兜底的：只搬走一半（例如统计行搬了、明细表留着）
    会让两处逻辑并存，而上面那条因为只扫 `report.py` 照样全绿。
    """
    src = (_ROOT / "run.py").read_text(encoding="utf-8")
    assert not _prints_mentioning(src, "latency_summary"), (
        "run.py 里还留着印汇总行的 print —— 应当只走 report.render_batch_report")
    assert "render_batch_report" in src, (
        "run.py 没有调用渲染层 —— 报告块是不是没搬完？")


def test_run_py_uses_the_shared_labels_constant():
    """标签**不能**在 run.py 里另写一份字面量（那样两边会漂移）。"""
    src = (_ROOT / "run.py").read_text(encoding="utf-8")
    assert "均值 / 中位 / 最快 / 最慢" not in src, (
        "run.py 里出现了硬编码的标签字面量 —— 应当用 report.LATENCY_LABELS")
    assert "LATENCY_LABELS" not in src, (
        "run.py 不该再直接碰标签常量 —— 印它的是 report.render_batch_report")


def test_run_py_no_longer_imports_statistics_locally():
    """修复前的写法在函数内 `import statistics as _st`，已移除。"""
    src = (_ROOT / "run.py").read_text(encoding="utf-8")
    assert "import statistics" not in src
