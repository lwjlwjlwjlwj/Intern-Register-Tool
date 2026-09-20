"""`tools/<子目录>/_path.py` 四份垫片必须**逐字节相同**。

为什么加这条（2026-09-20）
--------------------------
`tools/` 下的脚本按职责分在 4 个子目录里（`data/ gates/ ops/ probes/`），每个子目录
各有一份 `_path.py`。**这四份今天是逐字节相同的**（MD5 同为
`b5eebccf0862508eb28c4ed148e12588`）。

🔴 这个重复是**结构性的，不能消除** —— 垫片的职责就是"在 `sys.path` 被修正之前
先把 `tools/` 塞进去"，抽一个公共模块会变成鸡生蛋（要 import 公共模块，得先有
`sys.path`）。`tools/data/_path.py` 的注释也写明它**不能**改名为 `_bootstrap.py`
（会同名 import 到自己，报 `partially initialized module`）。

既然只能重复，就得防**分叉**：四个子目录的脚本用同一套垫片语义，一旦有人只改了
其中一份（比如给 probes 加一行调试输出），另外三个子目录的行为就悄悄不一样了，
而症状会是"某个子目录下的脚本能跑、另一个 ImportError"—— 排查方向完全错。

所以这里断言的不是"内容长什么样"，而是"四份必须一样"。

跑法：
    pytest tests/test_tools_shim_parity.py -v
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# tools/ 下当前的全部子目录。加新子目录时**必须**同步加进这里 ——
# 下面的 `test_every_tools_subdir_has_a_shim` 会盯着这件事。
TOOLS_SUBDIRS = ["data", "gates", "ops", "probes"]

SHIM = "_path.py"


def _shim_paths() -> list[Path]:
    return [ROOT / "tools" / d / SHIM for d in TOOLS_SUBDIRS]


def test_every_tools_subdir_has_a_shim():
    """子目录存在就必须有垫片 —— 漏一个的症状是那个目录下的脚本全 ImportError。"""
    missing = [str(p.relative_to(ROOT)) for p in _shim_paths() if not p.is_file()]
    assert not missing, f"这些子目录缺 {SHIM}：{missing}"


def test_shims_are_byte_identical():
    """四份必须逐字节相同（比 md5 更直接：直接比字节）。"""
    paths = _shim_paths()
    blobs = {p: p.read_bytes() for p in paths}
    ref_path = paths[0]
    ref = blobs[ref_path]

    diff = [str(p.relative_to(ROOT)) for p, b in blobs.items() if b != ref]
    assert not diff, (
        f"{SHIM} 分叉了 —— 与 {ref_path.relative_to(ROOT)} 不同的有：{diff}\n"
        "这四份是结构性重复（垫片要先于 sys.path 生效，抽不了公共模块），"
        "所以只能靠'保持一致'来防分叉。改一份就要改四份。"
    )


def test_each_shim_actually_exposes_root_and_tools():
    """护栏的下半：光"相同"不够 —— 四份一起被改坏也是"相同"的。

    🔴 这条**必须是行为断言，不能搜文本**。初稿写的是
    `assert "sys.path.insert" in body` —— 变异验证当场证明它没有判别力：
    一个把 `sys.path.insert(...)` 整个换成 `pass` 的 mutant **照样通过**
    （"sys.path.insert" 这几个字还留在 `if` 条件行里），而真正的失败模式
    ——"垫片插了个寂寞"—— 完全没被覆盖。

    现在的做法：**每个子目录各起一个子进程**真的 `import _path`，
    再断言 `tools/` 与仓库根都进了 `sys.path`。

    为什么用子进程而不是直接 import：四份垫片同名（都叫 `_path`），
    在同一个进程里 `sys.modules` 会缓存第一个，后三个等于没测。
    子进程同时把 `_bootstrap` 读 `.env` 的副作用挡在测试进程之外。
    """
    for d in TOOLS_SUBDIRS:
        shim_dir = ROOT / "tools" / d
        script = (
            "import json, sys\n"
            f"sys.path.insert(0, {str(shim_dir)!r})\n"
            "import _path  # noqa: F401  副作用：把 tools/ 与仓库根加进 sys.path\n"
            "print(json.dumps(sys.path))\n"
        )
        r = subprocess.run([sys.executable, "-c", script], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, (
            f"tools/{d}/_path.py 不能被正常 import（垫片坏了）：\n{r.stderr[-1500:]}"
        )
        paths = json.loads(r.stdout.strip().splitlines()[-1])
        assert str(ROOT / "tools") in paths, \
            f"tools/{d}/_path.py 没有把 tools/ 插进 sys.path"
        assert str(ROOT) in paths, \
            f"tools/{d}/_path.py 没有把仓库根插进 sys.path（_bootstrap.ROOT 没转出来）"



def test_shim_is_not_named_bootstrap():
    """🔴 反向断言：垫片**不能**叫 `_bootstrap.py`。

    `tools/data/_path.py` 的注释记录了这条：同名会 import 到自己，
    `sys.modules` 里已有半成品模块 ⇒
    `cannot import name 'ROOT' from partially initialized module`。
    这里把"不能改名"从注释升级成断言 —— 注释不会阻止有人"顺手统一命名"。
    """
    for d in TOOLS_SUBDIRS:
        bad = ROOT / "tools" / d / "_bootstrap.py"
        assert not bad.exists(), (
            f"{bad.relative_to(ROOT)} 不该存在 —— 垫片与它要转出的模块同名，"
            "会 import 到自己。见 tools/data/_path.py 的注释。"
        )
