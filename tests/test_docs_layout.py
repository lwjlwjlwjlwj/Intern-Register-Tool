"""文档布局元测试：拦住「搬完文档、引用悬空」这一类失效。

存在理由
========
本仓库**没有任何 markdown 锚点链接**（没有 `](#…)` 形式），章节之间的交叉引用
一律写成「见『X 节』」这种**纯文字**。于是 markdown 工具、链接检查器、
IDE 全都发现不了它断了 —— 只有人读的时候才会发现点不着。

2026-09-20（B8）把 README 的「协议要点」整段（735 行）搬到 `docs/protocol.md`，
**当场产生 8 处悬空引用**（README 里指向已迁走的节）。这类缺陷：
  · 不会让任何测试变红；
  · 不会让 CI 变红；
  · 只在有人想顺着读下去时暴露，而那时人已经不在上下文里了。

本文件把「引用必须能解析到真实标题」变成可执行断言。

⚠ 与 `tests/test_repo_hygiene.py` / `tests/test_dependency_surface.py` 同族：
  都是**元测试**，守的是"文档之间的约定"，不是业务行为。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
README = ROOT / "README.md"
PROTOCOL = DOCS / "protocol.md"
INDEX = DOCS / "README.md"

# 「见『X』」「详见『X』」「参见『X』」，允许中间夹一个 markdown 链接
REF_RE = re.compile(r"(?:详见|参见|见)\s*(?:\[[^\]]*\]\([^)]*\)\s*)?「([^」]+)」")
HEAD_RE = re.compile(r"^#{1,6}\s+(.*)$")


def _raw_lines(path: Path) -> list[tuple[int, str]]:
    """按行读，返回 `(真实行号, 文本)`，**不跳过代码围栏**。

    🔴 引用扫描必须用这个，不能用 `_lines()` —— 实测栽过：
       README 的「项目结构」是一大段 ``` 围栏里的目录树，其中
       `probe_captcha_timing.py ... （见「两条通路」）` 是一条**真引用**。
       用 `_lines()` 扫会把整段围栏跳掉 ⇒ 那条引用永远不被检查
       ⇒ 变异验证 M1（把 protocol 的标题改名让它悬空）**存活**（rc=0）。
       即：**"跳过围栏"对"找标题"是对的，对"找引用"是错的。**
    """
    return list(enumerate(path.read_text(encoding="utf-8").splitlines(), 1))


def _lines(path: Path) -> list[tuple[int, str]]:
    """按行读，返回 `(真实行号, 文本)`，并**丢掉代码围栏内的行**（用于找标题）。

    🔴 保留真实行号很重要：引用悬空时报的是 `README.md:99`，人得能直接跳过去。
       若在这里把围栏行滤掉又不记行号，报出来的行号就是错的。

    🔴 围栏里以 `#` 开头的行是**命令注释**，不是标题。README 里有 3 行这样的
       （`# 1) 生成槽位配置（顺带写出 slots.txt）` 等）。不跟踪围栏的话，
       它们会被当成标题，既污染标题池、又让引用检查失去意义。
       B8 搬运时正是栽在这里：朴素匹配得 27 个"标题"，真实只有 24 个。
    """
    out, in_fence = [], False
    for lineno, ln in _raw_lines(path):
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append((lineno, ln))
    return out


def _headings(path: Path) -> list[str]:
    return [m.group(1).strip() for _, ln in _lines(path) if (m := HEAD_RE.match(ln))]


def _level(ln: str) -> int | None:
    """该行的标题层级（1..6）；不是标题返回 None。"""
    m = HEAD_RE.match(ln)
    return len(ln) - len(ln.lstrip("#")) if m else None


def _norm(s: str) -> str:
    """去掉 markdown 强调记号后再比。

    🔴 少了这一步，护栏**全是假阳性**：标题写的是 `` `workers` 的边界 ``（带反引号）、
       `` `max_tokens` 给太小会把**好 key** 报成坏的 ``（带粗体），
       而正文引用写的是不带记号的形式。B8 第一次跑出 5 处"悬空"，**5 处全是这个原因**。
    """
    return re.sub(r"[`*_~]", "", s).strip()


def _docs_files() -> list[Path]:
    return sorted(DOCS.glob("*.md"))


def _all_references() -> list[tuple[str, int, str]]:
    """全仓 `.md` 里的「见『X』」引用 → (相对路径, 真实行号, X)。

    ⚠ 用 `_raw_lines`（**含**围栏），理由见它的 docstring —— 围栏里也有真引用。
    """
    refs = []
    for p in [README, *_docs_files()]:
        for lineno, ln in _raw_lines(p):
            for ref in REF_RE.findall(ln):
                refs.append((p.relative_to(ROOT).as_posix(), lineno, ref))
    return refs


# ────────────────────────────────────────────────────────────────
# [0] 扫描面守卫 —— 目录改名会让下面的参数化测试**静默消失**而不是失败
# ────────────────────────────────────────────────────────────────
def test_the_scan_surface_is_not_empty():
    """标题池与引用池都必须非空。

    这是本文件最重要的自保：`docs/` 改名 / README 改名 / 正则失效，
    都会让下面的断言变成**永远为真**的空转（0 个用例 = 全绿）。
    """
    heads = [h for p in [README, *_docs_files()] for h in _headings(p)]
    assert len(heads) > 50, f"标题池只有 {len(heads)} 个 —— 扫描面塌了"

    refs = _all_references()
    assert len(refs) >= 10, f"引用池只有 {len(refs)} 处 —— 扫描面塌了"


# ────────────────────────────────────────────────────────────────
# [1] 核心护栏：每一处「见『X』」都必须解析到真实标题
# ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "rel,lineno,ref", _all_references(), ids=lambda v: str(v)[:40]
)
def test_every_section_reference_resolves(rel, lineno, ref):
    """引用的节名必须真的存在（在同名标题里，或在更长的标题里）。

    判据是**包含关系**（双向）：引用常用简称（「两条通路」），标题是全称
    （「验证码有两条通路」），所以 `ref ⊆ heading` 或 `heading ⊆ ref` 都算解析。
    """
    pool = [_norm(h) for p in [README, *_docs_files()] for h in _headings(p)]
    r = _norm(ref)
    hits = [h for h in pool if r in h or h in r]
    assert hits, (
        f"{rel}:{lineno} 引用了「{ref}」，但全仓没有任何标题能解析它 —— "
        f"该节可能已被搬走（搬运后要同步改引用，见 docs/audit-2026-09-20.md §7「B8 执行记录」）"
    )


# ────────────────────────────────────────────────────────────────
# [2] 拆分后的结构约束
# ────────────────────────────────────────────────────────────────
def test_protocol_doc_exists_and_is_substantial():
    assert PROTOCOL.is_file(), f"缺 {PROTOCOL}"
    assert len(PROTOCOL.read_text(encoding="utf-8").splitlines()) > 500, \
        "protocol.md 太短 —— 协议要点整段（735 行）应在这里"


def test_protocol_has_exactly_one_h1():
    """`# 协议要点` 只能出现一次。

    B8 第一版就是这么错的：我加了 `# 协议要点` 作文件头，而搬来的块首行
    （由 `## 协议要点` 降级而来）**也叫这个** ⇒ 文件里出现两个 h1。
    """
    h1 = [m.group(1) for _, ln in _lines(PROTOCOL)
          if _level(ln) == 1 and (m := HEAD_RE.match(ln))]
    assert h1 == ["协议要点"], f"protocol.md 的 h1 应为唯一的『协议要点』，实得 {h1}"


def test_protocol_sections_live_in_exactly_one_place():
    """protocol.md 的每个 `##` 节都**不该**在 README 里也有同名标题。

    这条守的是拆分本身：搬完就搬完，不能两边各留一份（那正是 §5.1 说的
    "两处真相源"）。README 只留一个 `## 协议要点` 指针块 —— 注意它是 **h2**，
    而 protocol.md 的标题是 **h1**，所以不会被这条误伤。
    """
    proto_h2 = {_norm(m.group(1)) for _, ln in _lines(PROTOCOL)
                if _level(ln) == 2 and (m := HEAD_RE.match(ln))}
    assert proto_h2, "protocol.md 里一个 `##` 节都没有 —— 扫描面塌了"

    readme_heads = {_norm(h) for h in _headings(README)}
    overlap = proto_h2 & readme_heads
    assert not overlap, f"这些节在 README 与 protocol.md 各有一份：{sorted(overlap)}"


def test_readme_points_at_protocol():
    """README 的「协议要点」标题下必须紧跟一个能点到 protocol.md 的指针块。

    ⚠ 不能只断言"`docs/protocol.md` 出现在 README 里" —— README 里有 8 处引用
       都带着这个链接，删掉指针块也仍然为真（变异验证会存活）。
       判据必须锚在**指针块的位置**上。
    """
    body = README.read_text(encoding="utf-8")
    assert "## 协议要点" in body, "README 里没有「协议要点」标题"
    idx = body.index("## 协议要点")
    window = body[idx:idx + 600]
    assert "docs/protocol.md" in window, \
        "「协议要点」标题后 600 字内没有指向 docs/protocol.md 的指针块"


# ────────────────────────────────────────────────────────────────
# [3] docs/ 索引完整性
# ────────────────────────────────────────────────────────────────
def test_docs_index_exists():
    assert INDEX.is_file(), f"缺 {INDEX}"


@pytest.mark.parametrize("p", _docs_files(), ids=lambda p: p.name)
def test_every_doc_is_listed_in_the_index(p):
    """`docs/` 下每个 `.md` 都要在 `docs/README.md` 里被链到。

    防的是"加了新文档但没人知道它存在"。索引自己**不必**列自己。
    """
    if p.name == INDEX.name:
        pytest.skip("索引不必列自己")
    body = INDEX.read_text(encoding="utf-8")
    assert f"({p.name})" in body or f"/{p.name}" in body, \
        f"{p.name} 没被 docs/README.md 索引到"
