# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# On embarque les ressources nécessaires dans l'exe :
# - les assets de customtkinter (thèmes/polices) sinon l'interface plante au démarrage
# - le logo et l'icône utilisés par resource_path()
datas = collect_data_files('customtkinter')
datas += [('logo.png', '.'), ('icon.ico', '.')]

# minecraft_launcher_lib accède à ses sous-modules par attribut → on les force
hiddenimports = collect_submodules('minecraft_launcher_lib')

a = Analysis(
    ['launcher.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pypresence'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Communitycraft-Launcher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon.ico'],
)
