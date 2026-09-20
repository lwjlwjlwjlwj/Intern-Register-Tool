"""`src/fsutil.py` 的契约测试 + 三个调用点的**接线**测试。

为什么要这两半
--------------
`fsutil.atomic_write_text()` 是 2026-09-20 从**三处手写实现**归一出来的
（`ledger` 的台账落盘 / `proxypool` 的池子状态 / `quota` 的计数压缩）。
归一这件事本身有个典型的失败模式：**函数抽出来了，但调用点没接上**
（或者以后有人"顺手"改回内联）。那时 `fsutil` 自己的单测全绿，
而真正的风险面（那三个文件）又回到了"直接 write_text"。

所以本文件分两半：

  ① **契约**：`atomic_write_text` 本身的行为（含"写一半失败时目标文件必须
     保持旧内容"这条**唯一重要**的性质）；
  ② **接线**：三个模块确实把写盘交给了它 —— 用 spy 拦在 `fsutil` 的属性上。

⚠ 关于 ② 的 patch 目标：三个模块都是 `from . import fsutil` 后调
  `fsutil.atomic_write_text(...)`，所以 patch `fsutil.atomic_write_text`
  能拦到。**如果哪天有人改成 `from .fsutil import atomic_write_text`，
  这些 spy 会静默失效**（拦不到任何东西，但断言"至少调用过一次"仍可能
  因为别的原因通过）。为此 ② 里除了"拦到了"还断言了**调用参数**。

跑法：
    pytest tests/test_fsutil.py -v
"""

from pathlib import Path

import pytest

from src import fsutil, ledger, proxypool, quota


# ────────────────────────────────────────────────────────────────────
# ① 契约
# ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name,expected", [
    ("proxypool.json", "proxypool.json.tmp"),
    ("register_quota.jsonl", "register_quota.jsonl.tmp"),
    ("a.b.json", "a.b.json.tmp"),
    ("noext", "noext.tmp"),
])
def test_tmp_name_appends_and_never_replaces_the_suffix(name, expected):
    """🔴 临时名是**追加** `.tmp`，不是替换后缀。

    错成 `path.with_suffix(".tmp")` 时功能照常（临时文件照样被替换掉），
    所以这个 bug **不会**以"写盘失败"的形式暴露，只会在两种边缘情形下咬人：

      * `register_quota.jsonl` 的临时名变成 `register_quota.tmp` ——
        把"它是什么文件"这个信息抹掉了；
      * 同目录同时存在 `a.json` 与 `a.jsonl` 时，两者的临时名都变成
        `a.tmp` ⇒ **撞车**，后写的会把先写的临时文件覆盖掉。
    """
    assert fsutil.tmp_path_for(Path("/x") / name).name == expected


def test_creates_missing_parent_dirs(tmp_path):
    """父目录不存在时要建出来 —— 首次运行时 `ledger/runs/<日期>/` 就是这样。"""
    target = tmp_path / "a" / "b" / "c.json"
    fsutil.atomic_write_text(target, "{}")
    assert target.read_text(encoding="utf-8") == "{}"


def test_success_path_replaces_content_and_leaves_no_tmp(tmp_path):
    target = tmp_path / "state.json"
    target.write_text("OLD", encoding="utf-8")
    returned = fsutil.atomic_write_text(target, "NEW")

    assert returned == target
    assert target.read_text(encoding="utf-8") == "NEW"
    assert list(tmp_path.glob("*.tmp")) == [], "成功路径不该留下临时文件"


def test_non_ascii_round_trips(tmp_path):
    """默认 utf-8 —— 台账里全是中文与邮箱，编码错了会静默写成 `\\uXXXX`。"""
    target = tmp_path / "ledger.json"
    fsutil.atomic_write_text(target, '{"email": "张三@example.com"}')
    assert "张三" in target.read_text(encoding="utf-8")


def test_target_keeps_old_content_when_the_write_dies_midway(tmp_path, monkeypatch):
    """🔴 本文件**唯一重要**的一条：写到一半挂掉，目标文件必须还是旧内容。

    这是"原子写盘"这个词的全部含义，也是三处实现归一之后唯一必须保住的
    性质 —— 台账约 1 MB，写到一半被 Ctrl-C，留下的若是半截 JSON，下次
    `load_existing()` 返回 `[]` ⇒ 合并退化成"只有本次" ⇒ **静默丢台账**。

    ⚠ 构造方式：让 `Path.write_text` **先真写一部分、再抛**。
      不能只 `raise OSError` 而不写 —— 那样"先写坏数据再抛异常"的实现
      （即直接 `write_text` 到目标文件的版本）同样会通过，测试**没有判别力**。
      变异验证确认过：把实现换成直接写目标文件，这条会红。
    """
    target = tmp_path / "state.json"
    target.write_text("OLD", encoding="utf-8")

    real_write_text = Path.write_text

    def dying_write_text(self, data, **kw):
        real_write_text(self, str(data)[:2], **kw)   # 先落半截
        raise OSError("simulated crash mid-write")

    monkeypatch.setattr(Path, "write_text", dying_write_text)

    with pytest.raises(OSError):
        fsutil.atomic_write_text(target, "NEW-CONTENT")

    assert target.read_text(encoding="utf-8") == "OLD", (
        "目标文件被写成了半截 —— 说明实现是直接写目标文件，不是"
        "「先写临时文件再原子替换」。台账/状态文件一旦这样坏掉是**静默**的。"
    )


def test_old_tmp_is_cleaned_up_by_the_replace(tmp_path):
    """上一次崩溃留下的 `.tmp` 会被下一次成功写入覆盖掉，不会越积越多。"""
    target = tmp_path / "state.json"
    fsutil.tmp_path_for(target).write_text("STALE-PARTIAL", encoding="utf-8")

    fsutil.atomic_write_text(target, "NEW")

    assert target.read_text(encoding="utf-8") == "NEW"
    assert list(tmp_path.glob("*.tmp")) == []


# ────────────────────────────────────────────────────────────────────
# ② 接线：三个调用点确实走 fsutil
# ────────────────────────────────────────────────────────────────────
def _spy_on_fsutil(monkeypatch) -> list:
    """把 `fsutil.atomic_write_text` 换成记录调用的 spy。

    ⚠ 记的是 `(path, text)`，不是只记次数 —— 只断言"调用过"的话，
    一个"调了 fsutil 但内容写错/写错路径"的实现照样通过。
    """
    calls: list = []
    real = fsutil.atomic_write_text

    def spy(path, text, **kw):
        calls.append((Path(path), text))
        return real(path, text, **kw)

    monkeypatch.setattr(fsutil, "atomic_write_text", spy)
    return calls


def test_ledger_routes_through_fsutil(monkeypatch, tmp_path):
    """台账落盘（`ledger.save()`）必须走 `fsutil.atomic_write_text`。"""
    calls = _spy_on_fsutil(monkeypatch)
    out = tmp_path / "export.json"

    ledger.save(out, [{"email": "x@example.com"}], existing=[])

    assert len(calls) == 1, f"台账落盘没有走 fsutil（实际调用 {calls}）"
    assert calls[0][0] == out
    assert "x@example.com" in calls[0][1], "内容不对 —— 走了 fsutil 但写的不是它"


def test_ledger_snapshot_path_routes_through_fsutil(monkeypatch):
    """`save_snapshot()` 要写**两个**文件（快照 + latest），两次都走 fsutil。"""
    calls = _spy_on_fsutil(monkeypatch)

    ledger.save_snapshot([{"email": "x@example.com"}], existing=[])

    assert len(calls) == 2, f"应当恰好两次原子写，实际 {len(calls)}：{[c[0] for c in calls]}"


def test_proxypool_state_save_routes_through_fsutil(monkeypatch):
    """池子状态落盘（封禁一个槽位触发）必须走 `fsutil.atomic_write_text`。

    这一条覆盖的是最容易漏的那一处：`proxypool` 原先用
    `p.with_suffix(p.suffix + ".tmp")` 自己拼临时名，是三个实现里唯一
    "拼法不同但结果相同"的。
    """
    calls = _spy_on_fsutil(monkeypatch)
    slots = [f"http://127.0.0.1:790{i}" for i in range(1, 3)]

    pool = proxypool.ProxySlotPool(slots)
    pool.report_banned(pool.acquire(timeout=1, accept=lambda i: i == 1))

    assert len(calls) == 1, f"池子状态落盘没有走 fsutil（实际调用 {calls}）"
    assert calls[0][0] == proxypool.state_path()
    assert "cool_until" in calls[0][1], "内容不对 —— 不是池子状态"


def test_quota_compaction_routes_through_fsutil(monkeypatch):
    """配额计数压缩重写必须走 `fsutil.atomic_write_text`。

    ⚠ 这一处原先用的是 `tmp.replace(p)`（不是 `os.replace`），而且是三处里
    **唯一没有 mkdir** 的 —— 归一之后这两点都对齐了，这条测试钉住"确实接上了"。

    触发条件（见 `_compact_if_needed`）：总行数 ≥ 200 且窗口内用量 ≤ 总数一半。
    这里造 300 条**窗口外**的旧记录，于是 `used == 0`、`total == 300` ⇒ 触发压缩。
    """
    calls = _spy_on_fsutil(monkeypatch)

    p = quota.state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    old_ts = 1_600_000_000.0        # 远在 24h 窗口之外
    p.write_text("".join(f'{{"ts": {old_ts}, "email": "e{i}@x.com"}}\n'
                         for i in range(300)), encoding="utf-8")

    st = quota.status()
    assert st.used == 0 and len(quota._read_all()) == 300, "前置条件没造出来"
    quota._compact_if_needed(st)

    assert len(calls) == 1, f"配额压缩没有走 fsutil（实际调用 {calls}）"
    assert calls[0][0] == p
