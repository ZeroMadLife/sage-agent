# PyInstaller one-dir definition for the D0 macOS arm64 external binary.

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path.cwd()


def exclude_tests(module_name):
    return ".tests" not in module_name


hidden_imports = sorted(
    set(
        collect_submodules("aiosqlite", filter=exclude_tests)
        + collect_submodules("cryptography")
        + collect_submodules("langgraph.checkpoint.sqlite")
        + collect_submodules("psycopg2")
        + collect_submodules("sage_harness")
    )
)
datas = collect_data_files("certifi")
for distribution in (
    "aiosqlite",
    "cryptography",
    "fastapi",
    "httpx",
    "langgraph",
    "langgraph-checkpoint-sqlite",
    "psycopg2-binary",
    "uvicorn",
):
    datas += copy_metadata(distribution)

a = Analysis(
    [str(ROOT / "desktop" / "sidecar" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="sage-api-aarch64-apple-darwin",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="sage-api-aarch64-apple-darwin",
)
