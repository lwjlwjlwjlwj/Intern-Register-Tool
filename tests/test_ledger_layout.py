"""台账落盘布局（`ledger/` 目录 + 日期/时间戳快照）的契约与接线。

为什么单独一个文件
------------------
2026-09-20 把台账从"仓库根的一个 `results.json`"改成"`ledger/` 目录 +
`runs/<日期>/results-<时间戳>.json` 快照"；同一天又改了第二次：读源从
`ledger/latest.json` 挪到"**最新的那份全量快照**"，`latest.json` 降级成
**本批结果**。这个改动最危险的失败模式**不是崩溃，而是静默**：

  * 读源退回 `latest.json` ⇒ 它只含本批 ⇒ 合并基准只剩上一批 ⇒
    **台账停止累积、每次跑批覆盖上一次**（本项目栽过两次）；
  * 读源路径算错 ⇒ `load_existing()` 返回 `[]` ⇒ 合并等于没做 ⇒ 台账被切碎；
  * 快照里只存"本次那几条" ⇒ 下一次运行看不到历史 ⇒ `--count 1` 又变回 1 条；
  * `runs/` 里落了个杂文件被当成"最新快照" ⇒ 护栏分母与取样池跟着变；
  * 某个工具漏改默认路径 ⇒ 它读的是**一个不存在的文件** ⇒ 静默返回 `[]`。

全都不报错。所以这里钉两半：

  [1] 纯判据的**契约测试** —— 路径形状、落盘顺序、缩水护栏、快照是全量而非增量；
  [2] **静态接线检查** —— 读工具源码（AST），断言默认路径来自
      `ledger.ledger_path()`、没有任何地方再写死 `ROOT / "results.json"`、
      也没有人还在调已被移除的 `latest_path()` / `is_latest()`。

只做 [1] 不够：判据全绿、而某个工具根本没接上，是这类改动最常见的漏法。
（同一个教训在 `tests/test_report.py` 里也写过一遍：断言"调了判据"必须
**同时**断言"接线还在"，否则删掉整个分支测试照样绿。）
"""

import ast
import re
import time
from pathlib import Path

import pytest

from src import ledger

ROOT = Path(__file__).resolve().parents[1]

# 任意固定时刻（epoch 秒）。用常量而不是 `time.time()` —— 否则测试结果
# 依赖运行时刻，跨零点跑会红。
_WHEN = 1_758_320_000

# 快照相对 `ledger/` 的路径形状。
_SNAP_RE = re.compile(r"^runs/(\d{4}-\d{2}-\d{2})/results-(\d{8})-(\d{6})\.json$")

# 会**读**台账的每一处默认路径。新增工具时必须往这里加一行 ——
# 这就是"文件级静默缩水"的防线：某个工具漏改 ⇒ 它读一个不存在的文件 ⇒
# `load_existing()` 返回 `[]` ⇒ 它报告的一切都"正常"，只是没覆盖任何东西。
_LEDGER_CONSUMERS = [
    "run.py",
    "tests/conftest.py",
    "tools/run_downstream.py",
    "tools/data/recover_activation.py",
    "tools/data/prune_ledger.py",
    "tools/data/restore_results.py",
    "tools/data/migrate_quota_scope.py",
    "tools/ops/check_keys_alive.py",
    "tools/probes/probe_balance.py",
    "tools/probes/probe_login_only.py",
]


# ── [1] 契约：目录与两个落点 ────────────────────────────────────────
def test_default_ledger_dir_is_repo_root_ledger():
    """**默认**位置（未被测试夹具改写时）= 仓库根下的 `ledger/`。

    读源码而不是读 `ledger.LEDGER_DIR`：conftest 的 autouse 夹具会把它
    monkeypatch 到 tmp 目录（那是有意的隔离，见夹具里的注释），
    所以运行期拿不到默认值 —— 而"默认值是什么"恰恰是要钉住的东西。
    """
    src = (ROOT / "src" / "ledger.py").read_text(encoding="utf-8")
    assert re.search(r'^LEDGER_DIR\s*=\s*ROOT\s*/\s*"ledger"\s*$', src, re.M), (
        'src/ledger.py 里的 LEDGER_DIR 不再是 `ROOT / "ledger"`')


def test_last_run_path_is_ledger_dir_slash_latest_json():
    """`latest.json` 仍在原位、仍是这个名字，但**语义变了**：只含本批。"""
    assert ledger.LAST_RUN_NAME == "latest.json"
    assert ledger.last_run_path() == ledger.LEDGER_DIR / "latest.json"


def test_read_source_is_a_snapshot_not_latest_json():
    """读源必须是 `runs/` 里的快照，**不是** `latest.json`。

    这一条是本次改动的核心断言。读源退回 `latest.json` 会让台账停止累积
    （它只有本批那几十条），而现象是"每批都成功、台账条数不涨" —— 很晚才会
    有人发现。
    """
    snap, last = ledger.save_snapshot(
        [{"email": "a@example.com"}], existing=[], when=_WHEN)
    assert ledger.ledger_path() == snap
    assert ledger.ledger_path() != last
    assert ledger.ledger_path().name.startswith(ledger.SNAPSHOT_STEM + "-")


def test_ledger_path_without_snapshots_is_a_never_existing_placeholder():
    """空台账目录 ⇒ 返回**永不存在的占位路径**，而不是抛异常。

    `load_existing()` 的契约是"缺文件返回 `[]`"。抛异常会让一个还没跑过批次的
    目录把每个工具都挡在门外（包括 `prune_ledger` 这种只想看一眼的）。
    """
    p = ledger.ledger_path()
    assert not p.exists(), "占位路径不该真的存在"
    assert ledger.load_existing(p) == []
    # 占位路径仍在台账目录里 —— 否则 `is_ledger_path()` 会把它判成"导出到别处"。
    assert ledger.is_ledger_path(p)


# ── [1] 契约：快照路径的日期 / 时间戳 ───────────────────────────────
def test_snapshot_path_shape_and_internal_date_consistency():
    rel = ledger.snapshot_path(_WHEN).relative_to(ledger.LEDGER_DIR).as_posix()
    m = _SNAP_RE.match(rel)
    assert m, f"快照相对路径形状不对：{rel!r}"

    day, ymd, hms = m.groups()
    # 🔴 目录名与文件名里的日期**必须一致**。两处分别格式化是真实的漂移点：
    #    一处 `time.localtime`、另一处 `time.gmtime`，在 UTC+8 的凌晨会差一天，
    #    于是"9 月 20 日"那批快照躺在 `runs/2026-09-19/` 里。
    assert ymd == day.replace("-", ""), \
        f"目录 {day} 与文件名 {ymd} 的日期不一致"
    # 而且两者都来自同一个 `when`，不是"现在"。
    assert day == time.strftime("%Y-%m-%d", time.localtime(_WHEN))
    assert f"{ymd}-{hms}" == time.strftime("%Y%m%d-%H%M%S", time.localtime(_WHEN))


def test_snapshot_path_is_deterministic_and_driven_by_when():
    assert ledger.snapshot_path(_WHEN) == ledger.snapshot_path(_WHEN), \
        "同一个 when 两次调用应当给出同一个路径"
    assert ledger.snapshot_path(_WHEN) != ledger.snapshot_path(_WHEN + 1), \
        "when 变了路径就该变 —— 否则每次落盘都覆盖同一个文件，快照形同虚设"


@pytest.mark.parametrize("name", [
    "results-backup.json",          # 手工备份，glob 会命中、正则必须拒绝
    "results-zzz.json",             # 同上
    "results.json",                 # 老名字
    "notes.json",                   # 完全无关
    "results-2026-09-20.json",      # 日期带横线，不是 8 位
    "results-20260920.json",        # 只有日期没有时间
    "results-20260920-06123.json",  # 秒数 5 位
    "results-20260920-061230.json.bak",
])
def test_snapshot_name_regex_rejects_junk(name):
    """`runs/` 是个人工可写的目录，杂文件**不能**被当成台账读源。

    不筛的话，一个 `results-backup.json` 落进去就可能被选成"最新"，
    于是缩水护栏的分母、`--count` 的取样池全跟着变 —— 而且全程不报错。
    """
    assert not ledger.SNAPSHOT_RE.match(name), f"{name!r} 不该被当成合规快照名"


def test_snapshot_name_regex_accepts_the_canonical_name():
    assert ledger.SNAPSHOT_RE.match("results-20260920-061230.json")


# ── [1] 契约：读源怎么选 ────────────────────────────────────────────
def test_read_source_is_the_newest_snapshot_and_moves_after_each_write():
    """读源是**动态**的：每次落盘之后自动指向新快照。

    冻住（例如塞进模块级常量）会让 `run_downstream` 那种"读 → 跑 → 写回"
    的流程把结果写回**旧快照**，等于覆盖掉刚生成的那份。
    """
    first, _ = ledger.save_snapshot([{"email": "a@example.com"}], existing=[],
                                    when=_WHEN)
    assert ledger.ledger_path() == first

    second, _ = ledger.save_snapshot([{"email": "b@example.com"}], existing=[],
                                     when=_WHEN + 1)
    assert second != first
    assert ledger.ledger_path() == second, "落盘后读源没有跟着移动"


def test_read_source_is_chosen_by_name_not_mtime():
    """按**文件名里的时间戳**排序，不按 mtime。

    mtime 会被 `cp -p` / 解压 / 同步工具改掉，而文件名是落盘那一刻写死的。
    构造：先写"名字晚"的那份，再写"名字早"的那份 ⇒ 后者的 mtime 更新，
    但读源必须仍是**名字最晚**的那份。
    """
    later = ledger.snapshot_path(_WHEN + 3600)
    ledger.save_snapshot([{"email": "later@example.com"}], existing=[],
                         when=_WHEN + 3600)
    ledger.save_snapshot([{"email": "earlier@example.com"}], existing=[],
                         when=_WHEN)

    assert ledger.snapshot_path(_WHEN).stat().st_mtime >= \
        later.stat().st_mtime - 1, "前置条件不成立：mtime 顺序没反"
    assert ledger.ledger_path() == later, \
        "读源跟着 mtime 跑了 —— 应当时刻由文件名决定"


def test_stray_files_in_runs_are_not_treated_as_the_read_source():
    snap, _ = ledger.save_snapshot([{"email": "real@example.com"}], existing=[],
                                   when=_WHEN)
    for junk in ("results-backup.json", "results-zzz.json", "notes.json"):
        (snap.parent / junk).write_text("[]", encoding="utf-8")
    assert ledger.ledger_path() == snap


def test_snapshot_paths_is_sorted_and_ignores_junk():
    ledger.save_snapshot([{"email": "a@example.com"}], existing=[], when=_WHEN + 1)
    ledger.save_snapshot([{"email": "b@example.com"}], existing=[], when=_WHEN)
    (ledger.LEDGER_DIR / ledger.RUNS_DIRNAME / "2026-01-01").mkdir(
        parents=True, exist_ok=True)
    (ledger.LEDGER_DIR / ledger.RUNS_DIRNAME / "2026-01-01"
     / "results-backup.json").write_text("[]", encoding="utf-8")

    got = ledger.snapshot_paths()
    assert len(got) == 2, f"应当只认出 2 份合规快照，实际 {len(got)}：{got}"
    assert got == sorted(got), "没有按时间升序"
    assert got[-1] == ledger.ledger_path()


# ── [1] 契约：落盘行为 ──────────────────────────────────────────────
def test_save_snapshot_writes_full_snapshot_and_batch_to_latest():
    """两个文件的**内容不同**，这是本次改动的要点。

    快照 = 合并后的**全量**（台账）；`latest.json` = **本批**（含失败 / 跳过）。
    """
    old = [{"email": f"old{i}@example.com", "status": "success"} for i in range(5)]
    ledger.save_snapshot(old, existing=[], when=_WHEN)

    existing = ledger.load_existing(ledger.ledger_path())
    batch = [{"email": "new@example.com", "status": "failed"}]
    merged, kept, added, _ = ledger.merge_records(existing, batch)
    assert (kept, added) == (5, 1), "前置条件不成立：合并没拿到历史"

    snap, last = ledger.save_snapshot(merged, batch, when=_WHEN + 1)

    assert len(ledger.load_existing(snap)) == 6, "快照必须是合并后的全量 6 条"
    assert ledger.load_existing(last) == batch, \
        "latest.json 必须是本批（1 条失败），不是全量"
    assert ledger.ledger_path() == snap, "新快照没有成为读源"


def test_batch_defaults_to_full_when_not_given():
    """`batch` 不传 ⇒ `latest.json` 与快照同内容。

    `prune_ledger` / `restore_results` / `run_downstream` 是"整本台账重写"，
    没有"本批"这个概念，它们不该被迫造一个。
    """
    recs = [{"email": "a@example.com"}, {"email": "b@example.com"}]
    snap, last = ledger.save_snapshot(recs, existing=[], when=_WHEN)
    assert snap.read_bytes() == last.read_bytes()


def test_ledger_keeps_accumulating_across_two_runs():
    """连续两批之后，读源里必须是**两批的并集**。

    这是"`latest.json` 只留本批"唯一可能引入的真事故：读源若退回
    `latest.json`，第二次运行只看得到第一批 ⇒ 台账被切碎、条数不涨。
    """
    b1 = [{"email": "a@example.com", "status": "success"}]
    m1, _, _, _ = ledger.merge_records(ledger.load_existing(ledger.ledger_path()), b1)
    ledger.save_snapshot(m1, b1, when=_WHEN)

    # 模拟"下一批"：**只**通过公开 API 拿历史
    existing = ledger.load_existing(ledger.ledger_path())
    assert [r["email"] for r in existing] == ["a@example.com"], (
        "读源只看到本批 —— 台账会停止累积（这正是 `latest.json` 不能当读源的原因）")
    b2 = [{"email": "b@example.com", "status": "success"}]
    m2, kept, added, _ = ledger.merge_records(existing, b2)
    assert (kept, added) == (1, 1)
    ledger.save_snapshot(m2, b2, when=_WHEN + 1)

    assert len(ledger.load_existing(ledger.ledger_path())) == 2
    assert len(ledger.load_existing(ledger.last_run_path())) == 1, \
        "latest.json 应当只留最后那批"


def test_shrink_guard_raises_before_writing_anything():
    """缩水护栏必须在**任何写入之前**生效。

    判据不只是"抛了 ValueError" —— 还要**没有任何文件被创建 / 改动**。
    一个"先写快照、再检查"的实现同样会抛异常，但坏数据已经躺在磁盘上了。
    """
    ledger.save_snapshot([{"email": "keep@example.com"}], existing=[], when=_WHEN)
    before = ledger.last_run_path().read_bytes()

    with pytest.raises(ValueError, match="防静默缩水"):
        ledger.save_snapshot([], existing=[{"email": "keep@example.com"}],
                             when=_WHEN + 1)

    assert ledger.last_run_path().read_bytes() == before, "latest.json 被改动了"
    assert not ledger.snapshot_path(_WHEN + 1).exists(), \
        "护栏抛了异常，却还是把快照写下去了"


def test_existing_none_reads_the_read_source_not_latest_json():
    """`existing=None` 以**读源**为准，不是 `latest.json`。

    🔴 这个用例**必须让两者的条数不同**，否则两种实现给出同一个分母，
      测了等于没测（第一版就踩了这个坑）。构造：快照 3 条、本批 1 条 ⇒
      落 2 条时两条路分道扬镳：
        以读源（3 条）为准 ⇒ 2 < 3 拦下；
        以 latest.json（1 条）为准 ⇒ 2 ≥ 1 放行。
    """
    merged = [{"email": f"m{i}@example.com"} for i in range(3)]
    batch = [{"email": "m0@example.com"}]
    ledger.save_snapshot(merged, batch, existing=[], when=_WHEN)

    assert len(ledger.load_existing(ledger.ledger_path())) == 3
    assert len(ledger.load_existing(ledger.last_run_path())) == 1

    with pytest.raises(ValueError, match="防静默缩水"):
        ledger.save_snapshot([{"email": "x@example.com"}] * 2, when=_WHEN + 1)


def test_snapshot_is_written_before_latest(monkeypatch):
    """先写快照、再刷 `latest.json`。

    反过来的话，第二步失败就变成"读源已更新、快照没留下" —— 这次落盘没有
    回滚点，而 `latest.json` 已经是新内容，人工也退不回去。
    """
    calls = []
    real = ledger._atomic_write

    def spy(path, records):
        calls.append(Path(path))
        return real(path, records)

    monkeypatch.setattr(ledger, "_atomic_write", spy)
    ledger.save_snapshot([{"email": "x@example.com"}], existing=[], when=_WHEN)

    assert len(calls) == 2, f"应当恰好写两个文件，实际 {len(calls)} 个：{calls}"
    assert calls[0] == ledger.snapshot_path(_WHEN), "第一个写的不是快照"
    assert calls[1] == ledger.last_run_path(), "最后刷新的不是 latest.json"


def test_plain_save_does_not_archive(tmp_path):
    """`save()` 写显式路径时**不落快照** —— 导出到别处不该污染台账历史。"""
    p = tmp_path / "export.json"
    ledger.save(p, [{"email": "x@example.com"}], existing=[])
    assert p.is_file()
    assert not (ledger.LEDGER_DIR / ledger.RUNS_DIRNAME).exists(), \
        "save() 不该建快照目录"


# ── [1] 契约：`is_ledger_path()` ────────────────────────────────────
def test_is_ledger_path_covers_the_whole_ledger_dir(tmp_path):
    """判据是"在不在台账目录里"，**不是**"等不等于读源"。

    用相等比较会漏：读源每次落盘都换名字，第二次落盘之后 `--out` 传进来的
    旧路径就"不是台账"了 ⇒ 工具退化成只写那一个文件、不留快照。
    """
    assert ledger.is_ledger_path(ledger.last_run_path())
    assert ledger.is_ledger_path(ledger.snapshot_path(_WHEN))
    assert ledger.is_ledger_path(ledger.ledger_path())
    assert ledger.is_ledger_path(str(ledger.last_run_path())), "字符串形式也要认"
    assert ledger.is_ledger_path(
        ledger.LEDGER_DIR / ledger.RUNS_DIRNAME / "2020-01-01"
        / "results-20200101-000000.json"), "旧快照也算台账"

    assert not ledger.is_ledger_path(tmp_path / "export.json")
    assert not ledger.is_ledger_path(ledger.ROOT / "keys.json")


# ── [2] 静态接线：run.py 的 --out ───────────────────────────────────
def _out_arg_default() -> object:
    """从 `run.py` 的 AST 里取 `add_argument("--out", ...)` 的 `default`。

    用 AST 而不是搜文本：文本搜 `default=None` 会撞上任何一个别的参数的默认值。
    """
    tree = ast.parse((ROOT / "run.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        if node.args[0].value != "--out":
            continue
        return {k.arg: k.value for k in node.keywords}.get("default", None)
    pytest.fail("run.py 里找不到 add_argument('--out', ...)")


def test_run_py_out_default_is_none_and_wired_to_snapshot():
    default = _out_arg_default()
    assert isinstance(default, ast.Constant) and default.value is None, (
        "`--out` 的默认值必须显式是 None（不填 = 走台账目录）；"
        f"实际是 {ast.dump(default) if default is not None else '缺失'}")

    src = (ROOT / "run.py").read_text(encoding="utf-8")
    assert "save_snapshot" in src, "run.py 没有接上 save_snapshot()"
    assert "_ledger.ledger_path()" in src, \
        "run.py 读台账没有走 ledger_path()（读源 = 最新快照）"


def test_run_py_passes_this_batch_to_latest_json():
    """`run.py` 必须把**本批原始结果**传进去当 `batch`。

    只传 `merged` 的话，`latest.json` 会变成全量副本 —— 那就退回改之前的样子，
    "本批结果"这个用途又没了（而它恰恰是这次改动的目的）。
    AST 判据：`save_snapshot(...)` 的调用里必须有两个位置实参。
    """
    tree = ast.parse((ROOT / "run.py").read_text(encoding="utf-8"))
    hits = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "save_snapshot"):
            hits.append(node)
    assert len(hits) == 1, f"应当恰好一处 save_snapshot 调用，实际 {len(hits)} 处"
    assert len(hits[0].args) == 2, (
        "save_snapshot 只传了 1 个位置实参 —— 本批结果没传进去，"
        "`latest.json` 会退化成全量副本")


# ── [2] 静态接线：所有消费者都别写死退役路径 / 别调已移除的 API ──────
def _hardcoded_retired_paths(rel: str) -> list:
    """文件里**真的写了** `ROOT / "results.json"` 这个表达式的位置（行号）。

    🔴 必须走 AST，不能搜文本。第一版就是 `assert 'ROOT / "results.json"' not in src`，
    结果被**自己写的注释**绊倒 —— `tests/conftest.py` 里那句
    「不要写 `ROOT / "results.json"`」当场把自己判红。
    这类"文档在教人别这么写、检查却按字面拦"的假阳性，逼出来的修法是
    **把有用的注释删掉**，而那条注释正是下次再改布局时唯一会提醒人的东西。
    假阳性比漏检更坏：漏检只是没守住，假阳性会主动破坏正确的代码。
    """
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Div):
            continue
        left, right = node.left, node.right
        if (isinstance(left, ast.Name) and left.id == "ROOT"
                and isinstance(right, ast.Constant)
                and right.value == "results.json"):
            hits.append(node.lineno)
    return hits


def _calls_removed_ledger_api(rel: str) -> list:
    """文件里还在调 `…latest_path()` / `…is_latest()` 的位置（行号）。

    这两个 API 在 2026-09-20 第二次改布局时被移除（读源变成"最新快照"）。
    漏改的调用点会 `AttributeError`，但那是在**运行时**、而且是在某个工具
    真正被执行时 —— 静态检查能提前抓到，顺带覆盖 `--help` 都跑不到的分支。
    走 AST 而不是搜文本：注释里解释"别再用 `latest_path()`"是正常的。
    """
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    removed = {"latest_path", "is_latest"}
    return [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and n.attr in removed]


def test_hardcoded_path_check_ignores_comments_and_docstrings():
    """判据本身要经得起"注释 / 文档字符串里写着这个表达式"。

    `tests/conftest.py` 的注释里**有**这个字面量，而它的代码是对的 ——
    这里钉住：那种情况不算命中。
    """
    assert _hardcoded_retired_paths("tests/conftest.py") == []


def test_removed_api_check_ignores_comments_and_docstrings():
    """同上：注释里提到 `latest_path()` 不算"还在调它"。"""
    assert _calls_removed_ledger_api("src/ledger.py") == []
    assert _calls_removed_ledger_api("run.py") == []


@pytest.mark.parametrize("rel", _LEDGER_CONSUMERS)
def test_no_consumer_hardcodes_the_retired_path(rel):
    hits = _hardcoded_retired_paths(rel)
    assert not hits, (
        f"{rel} 第 {hits} 行还写死了已退役的 `ROOT / \"results.json\"` —— "
        f"它读不到新台账，`load_existing()` 会静默返回空列表")
    assert "ledger.ledger_path()" in (ROOT / rel).read_text(encoding="utf-8"), \
        f"{rel} 没有用 `ledger.ledger_path()` 取台账读源"


@pytest.mark.parametrize("rel", _LEDGER_CONSUMERS)
def test_no_consumer_calls_the_removed_api(rel):
    hits = _calls_removed_ledger_api(rel)
    assert not hits, (
        f"{rel} 第 {hits} 行还在调已被移除的台账 API"
        f"（`latest_path()` / `is_latest()`）—— 读源已改成"
        f" `ledger.ledger_path()`（最新快照）、落盘判据改成 `is_ledger_path()`")


def test_removed_api_is_really_gone():
    """被移除的 API 不该还留在 `src/ledger.py` 里。

    留着 `latest_path()` 当别名会更糟：它名字里带 "latest"，读者会以为它指向
    `latest.json`，而实际语义已经变了 —— 这正是本次改动要消灭的那类陷阱。
    """
    src = (ROOT / "src" / "ledger.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    funcs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert "ledger_path" in funcs
    assert "last_run_path" in funcs
    assert "is_ledger_path" in funcs
    assert "latest_path" not in funcs, "`latest_path` 应当已被移除"
    assert "is_latest" not in funcs, "`is_latest` 应当已被移除"
