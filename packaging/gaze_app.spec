# PyInstaller spec - build with packaging/build.ps1 (one folder, windowed).
# Code is bundled; config.yaml and data/ are copied NEXT TO the executable by build.ps1 so the
# lab can edit them (and the Author tool can write them). Sessions and logs are written there too.
# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = SPECPATH + "/.."

a = Analysis(
    [ROOT + "/app/main.py"],
    pathex=[ROOT],
    datas=collect_data_files("qtawesome") + collect_data_files("pyqtgraph"),
    hiddenimports=collect_submodules("app") + ["google.genai"],
    excludes=["tkinter", "pytest", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="GazeAdaptiveStudy", console=False,
          debug=False, strip=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="GazeAdaptiveStudy")
