"""`quota.shortfall_hint()` 的**差分等价 + 契约**测试。

背景（2026-09-20 实测）
-----------------------
`--ignore-quota` 那一批 **47/50 成功、0 跳过**，而日志第 7 行却写着：

    所有出口额度都已用尽，这一批会全部被跳过（未发请求）。

**这句话是假的**，而且假得很危险 —— 它把人引向"为什么全跳过了"这个
不存在的问题。根因：非槽位分支早就用 `not args.ignore_quota` 挡过，
**槽位分支的两个分支都漏了**（`total_left == 0` 与 `total_left < planned`）。

这与同一天修的 `QuotaGovernor.check_slot/claim_slot` 是**同一类缺陷**：
开关只覆盖了一部分决策点（见 skill `feature-flag-partial-coverage-audit`）。

⚠ 判据为什么在 `src/quota.py` 而不是 `run.py`
--------------------------------------------
最初写进了 `run.py` 的模块级，测试 `from run import …` —— 结果
`test_dependency_surface.py` 当场变红：

    ✗ run   ← tests/test_run_quota_hint.py → run.quota_shortfall_hint

`run.py` 会把整套 pipeline 拉进 `sys.path`，**测试链不该 import CLI 模块**。
移到 `src/quota.py` 后依赖面不变（那个模块本来就在测试链上）。
⇒ 这条是那个元测试**正好抓到它该抓的东西**的实例，别把它读成误报。

两部分测试
----------
1. **差分**（`ignore_quota=False`）：`_ref_hint` 是从 `run.py` 逐字抄下来的
   旧内联分支，断言新旧结论**逐字一致** —— 证明这次改动**没有动**
   非 ignore 路径的行为（那段话术本身是对的，不该顺手改）。
2. **契约**（`ignore_quota=True`）：断言"**永不承诺会跳过**"。
   ⚠ 这一半**不能**用差分：旧逻辑压根没有 ignore 分支，拿它当参照物
   只会把洞固化成"预期行为"。

跑法：
    pytest tests/test_run_quota_hint.py -v
"""

import pytest

from src.quota import shortfall_hint

PLANNED = 50


def _ref_hint(total_left: int, planned: int) -> str:
    """旧内联分支（`git show HEAD:run.py`，槽位模式那段）的逐字抄写。

    ⚠ **刻意没有 `ignore_quota` 参数** —— 旧逻辑就没读它，这是历史遗留的洞。
    不要"补"上去：它是参照物，补了就再也照不出这个差异。
    """
    if total_left == 0:
        return ("   ⚠ 所有出口额度都已用尽，这一批会全部被跳过（未发请求）。\n"
                "     等窗口滑出，或加 --ignore-quota（有被目标站封 IP 的风险）。")
    if total_left < planned:
        return (f"   ⚠ 可用额度 {total_left} < 计划 {planned}，"
                f"会有约 {planned - total_left} 个被跳过（未发请求）。")
    return ""


# ══════════════════════════════════════════════════════════════════
# 1. 差分：关着开关时，话术必须一字未改
# ══════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("left", [0, 1, 3, 25, 49, 50, 60])
def test_hint_matches_reference_when_flag_off(left):
    """`ignore_quota=False` ⇒ 与旧内联分支**逐字**一致。

    这一段话术本身是对的（那时确实会跳过），**不该顺手改**。
    """
    old = _ref_hint(left, PLANNED)
    new = shortfall_hint(left, PLANNED, False)
    assert new == old, f"left={left}\n旧: {old!r}\n新: {new!r}"


def test_reference_does_not_know_about_the_flag():
    """钉住"参照物缺 ignore"这个事实本身 —— 别有人去"修"它。"""
    import inspect
    assert "ignore" not in inspect.signature(_ref_hint).parameters


# ══════════════════════════════════════════════════════════════════
# 2. 契约：开着开关时，永不承诺"会跳过"
# ══════════════════════════════════════════════════════════════════
# 这两条是"假话"的指纹 —— 开关打开时它们**都不许出现**。
FORBIDDEN = ("会全部被跳过", "会有约")


@pytest.mark.parametrize("left", [0, 1, 3, 25, 49, 50, 60])
def test_flag_on_never_promises_skips(left):
    """🔴 这是那次 47/50 却被报"全部跳过"的直接复现。"""
    hint = shortfall_hint(left, PLANNED, True)
    for bad in FORBIDDEN:
        assert bad not in hint, (
            f"left={left}：--ignore-quota 开着还说 {bad!r} = 开关失效\n{hint}")


@pytest.mark.parametrize("left", [0, 1, 49])
def test_flag_on_explains_why_nothing_is_skipped(left):
    """额度不足时要说清"为什么不会跳过"，而不是闷着不提示。"""
    hint = shortfall_hint(left, PLANNED, True)
    assert hint, f"left={left}：额度不足却没有任何提示"
    assert "--ignore-quota" in hint, "提示里要出现开关名，人才知道该关掉谁"
    assert "不会因此跳过" in hint


def test_flag_on_and_enough_quota_stays_silent():
    """额度够 ⇒ 什么都不说（开关打开与否都不该刷屏）。"""
    assert shortfall_hint(PLANNED, PLANNED, True) == ""
    assert shortfall_hint(PLANNED + 10, PLANNED, True) == ""
    assert shortfall_hint(PLANNED, PLANNED, False) == ""


def test_only_the_flag_differs():
    """同输入下，开关是**唯一**变量 —— 差异必须只出现在"是否承诺跳过"上。

    防止将来有人把 ignore 分支写成"什么都不打印"：那样 `left` 的变化
    会被静默吞掉，额度不足时反而没提示。
    """
    for left in (0, 1, 49):
        on = shortfall_hint(left, PLANNED, True)
        off = shortfall_hint(left, PLANNED, False)
        assert on != off, f"left={left}：开关没造成任何差异"
        assert off and on, f"left={left}：两边都该有提示"
