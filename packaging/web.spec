"""PyInstaller 单文件打包 spec —— Web 控制台。

用法（Windows / Linux 一致）：
    python -m pip install pyinstaller
    python -m PyInstaller packaging/web.spec --noconfirm --distpath dist

产物：dist/InternRegisterWeb(.exe) —— 单个可执行文件，双击即启动：
  1. 自动打开默认浏览器访问 http://127.0.0.1:8000
  2. 配置持久化在 ~/.intern-register-tool/webui-data/config.json
  3. 首次使用在网页「⚙️ 应用设置」里填入凭据（worker 或 yyds 二选一）
"""

from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent

datas = [
    (str(ROOT / "webui" / "templates"), "webui/templates"),
    (str(ROOT / "webui" / "static"), "webui/static"),
]

a = Analysis(
    [str(ROOT / "web.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        # webui/clients.py 里对 src 的导入是函数内延迟导入，静态分析看不到，
        # 这里显式钉住，保证 yyds 模式在打包后可用。
        "src.yyds_client",
        "src.yydsmail",
        "src.config",
        "src.base",
        # uvicorn 按协议/事件循环分派，需要把分支模块一起带上。
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.lifespan",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="InternRegisterWeb",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
