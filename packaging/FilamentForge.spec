# PyInstaller recipe: builds a single FilamentForge(.exe).
#   pyinstaller packaging/FilamentForge.spec
# Your data is NOT inside the exe; it lives in the per-user folder (see forge/paths.py),
# so replacing the exe with a newer one keeps everything, including the AI.
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent  # noqa: F821 (SPECPATH is set by PyInstaller)

hidden = (
    collect_submodules("uvicorn")
    + collect_submodules("forge")
    + ["python_multipart", "multipart", "segno", "pypdf", "PIL.ImageFont", "zxingcpp"]
)

a = Analysis(  # noqa: F821
    [str(ROOT / "run.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "forge" / "static"), "forge/static")],
    hiddenimports=hidden,
    excludes=["tkinter", "pytest", "cv2", "numpy"],
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="FilamentForge",
    console=True,   # the window shows the address; closing it quits the app (and stops the AI)
    upx=False,
)
