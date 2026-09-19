"""`src/ledger.key_coverage` —— 「导出快照 vs 权威台账」的覆盖统计。离线，零网络。

为什么值得单独钉住
------------------
`tools/ops/check_keys_alive.py` 的默认输入是**某次导出的 CSV 快照**，
它只报"存活 N/N"。N 是**快照自己的**分母 —— 快照整体过期时，
这个数字看着全绿，实则完全没覆盖当时那一批。

2026-09-20 实测踩到：快照停在 3 天前、只有 53 把，而台账里已有 407 把，
跑出 **"53/53 存活"** 这种"没测到却像全绿"的结论。

这不是新问题，是**同一个文件里已有护栏的高一层版本**：
`check_keys_alive.py` 早就对"行级静默缩水"（过滤掉非 `sk-` 行）做了报数，
但**文件级缩水没人管** —— 而行级缩水至少分母变了，文件级缩水连分母本身都是错的。

覆盖方向是**单向**的
--------------------
`missing` 只表达「台账有、清单没有」。反方向（清单有而台账没有）本函数不判 ——
`test_direction_is_one_way` 专门把这个契约钉住，防止后来者把"missing 为空"
误读成"两边一致"。测试链不含 `tools/`，所以这里**不 import 那个工具**：
纯判据在 `src/ledger.py`，本文件只测它。
"""

from pathlib import Path

import pytest

from src import ledger

# 仓库根 —— 静态接线检查要用（见文件末尾）。
_ROOT = Path(__file__).resolve().parents[1]


# ── 契约：missing 的方向 ────────────────────────────────────────────

def test_fully_covered_means_empty_missing(any_ledger):
    """清单 == 台账的 key 全集 ⇒ 没有遗漏（这是"快照是最新的"这一档）。"""
    keys = {r["api_key"] for r in any_ledger if r.get("api_key")}
    ledger_n, known_n, missing = ledger.key_coverage(any_ledger, keys)
    assert missing == set(), f"清单已含全部台账 key，却报出遗漏 {len(missing)} 把"
    assert ledger_n == known_n, "两边都是全量，计数应当一致"


def test_stale_snapshot_is_detected(any_ledger):
    """🔴 本次要防的真实场景：快照只覆盖了前 3 把。

    断言的是**告警会触发**（`missing` 非空）—— 这正是旧版本做不到的事。
    """
    keys = [r["api_key"] for r in any_ledger if r.get("api_key")]
    if len(keys) < 4:
        pytest.skip("台账里带 key 的记录太少，构不出'快照落后'的场景")

    ledger_n, known_n, missing = ledger.key_coverage(any_ledger, set(keys[:3]))
    assert known_n == 3
    assert len(missing) == ledger_n - 3, (
        f"台账 {ledger_n} 把、快照 3 把 ⇒ 应报出 {ledger_n - 3} 把遗漏，"
        f"实际 {len(missing)}"
    )
    assert missing, "快照远落后于台账，却没有报出任何遗漏 —— 护栏失效"


def test_direction_is_one_way():
    """清单里有、台账里没有的 key，**不得**出现在 missing 里。

    契约：missing = 台账 − 清单。反方向是"台账被覆盖过"的另一个问题，
    本函数刻意不判。别把 missing 为空读成"两边一致"。
    """
    records = [{"email": "a@x", "api_key": "sk-ledger1"}]
    _, known_n, missing = ledger.key_coverage(records, {"sk-ledger1", "sk-orphan"})
    assert known_n == 2, "清单计数应当把孤儿 key 也算进去（它是清单自己的分母）"
    assert missing == set(), f"孤儿 key 不该出现在 missing 里，却出现了 {missing}"


# ── 契约：计数口径 ──────────────────────────────────────────────────

def test_non_prefixed_keys_are_ignored():
    """不以 `sk-` 开头的 `api_key` 不计入 —— 与工具的行级过滤同一套前缀。"""
    records = [
        {"api_key": "sk-good"},
        {"api_key": "pk-other"},       # 前缀不对
        {"api_key": "notakeyatall"},
        {"api_key": "sk-good"},        # 重复：集合去重，计数仍是 1
    ]
    ledger_n, known_n, missing = ledger.key_coverage(records, {"sk-good"})
    assert ledger_n == 1, f"只有一把 sk- key，却数成 {ledger_n}"
    assert known_n == 1
    assert missing == set()


def test_records_without_api_key_are_ignored():
    """缺失 / 空 / None 的 `api_key` 一律不计 —— 它们不是 key，不是"死 key"。"""
    records = [
        {"email": "a@x"},                      # 没有这个字段
        {"email": "b@x", "api_key": ""},       # 空串
        {"email": "c@x", "api_key": None},     # None
        {"email": "d@x", "api_key": "sk-real"},
    ]
    ledger_n, _, _ = ledger.key_coverage(records, set())
    assert ledger_n == 1, f"只有 1 条真 key，却数成 {ledger_n}"


def test_non_dict_records_are_ignored():
    """台账里混进非 dict（手改坏了）不该让统计炸掉。

    `load_existing` 已经过滤过一层，但 `key_coverage` 是公开函数，
    调用方可能直接喂原始 list。
    """
    records = ["oops", 42, None, {"api_key": "sk-ok"}]
    ledger_n, _, _ = ledger.key_coverage(records, set())
    assert ledger_n == 1


def test_empty_known_keys_do_not_inflate_count():
    """清单里的 `None` / `""` 不算 key —— 否则分母会虚高，覆盖率被算得更好看。"""
    records = [{"api_key": "sk-a"}]
    ledger_n, known_n, missing = ledger.key_coverage(records, {None, "", "sk-a"})
    assert known_n == 1, f"空值混进清单计数，known_n={known_n}"
    assert missing == set()


def test_prefix_constant_is_shared_and_conventional():
    """前缀只定义一次，值是 `sk-`。改了它会同时改变过滤与统计口径。"""
    assert ledger.KEY_PREFIX == "sk-"
    # 用常量本身构造输入：前缀一旦被改，这个用例跟着改，不会偷偷不一致
    assert ledger.key_coverage([{"api_key": ledger.KEY_PREFIX + "x"}], set())[0] == 1


# ── 边界 ────────────────────────────────────────────────────────────

def test_empty_inputs():
    """两个输入都空 ⇒ `(0, 0, set())`，且 `missing` 是空集不是 `None`。

    ⚠ 空输入**不是**"数据没问题"的证据 —— 它只说明没有可比的东西。
    调用方看到 `(0, 0, set())` 必须自己判断台账是不是根本没读到。
    """
    assert ledger.key_coverage([], set()) == (0, 0, set())


def test_returns_plain_set_not_falsy_sentinel():
    """返回值类型契约：`missing` 是可做集合运算的 `set`。"""
    _, _, missing = ledger.key_coverage([{"api_key": "sk-a"}], {"sk-b"})
    assert isinstance(missing, set)
    assert missing == {"sk-a"}
    assert "sk-b" not in missing


# ── 接线：护栏真的被工具调用了 ──────────────────────────────────────

TOOL = _ROOT / "tools" / "ops" / "check_keys_alive.py"


def test_tool_actually_wires_the_guard():
    """静态接线检查：纯函数测绿 **≠** 工具真的调了它。

    只能做静态检查 —— 跑那个工具要发真实网络请求，不适合放进测试链。

    ⚠ 它证明的是"接线还在"，**不是**"告警文案对"：
      把这段删掉会红；把文案改丑**不会**红。
      这是本用例的已知盲区，别把它读成"告警一定会在正确的时机打印"。
    """
    src = TOOL.read_text(encoding="utf-8")
    assert "ledger.key_coverage(" in src, "工具没调用 key_coverage —— 文件级护栏被摘了"
    assert "ledger.load_existing(" in src, "工具没读台账 —— 覆盖比对失去参照物"
    assert "KEY_PREFIX = ledger.KEY_PREFIX" in src, (
        "前缀没共用同一处定义：行级过滤与覆盖统计会各认一套口径，互相掩盖"
    )
    # 光"算了覆盖"不够 —— 得**真的告警**。少了这一条，把整个 `if missing:`
    # 告警块删掉（保留计算）本用例仍会绿，而那正是护栏失效的主路径。
    assert "if missing:" in src, "算了覆盖却没有告警分支 —— 护栏等于没接"
