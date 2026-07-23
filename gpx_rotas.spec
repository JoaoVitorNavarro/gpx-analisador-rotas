# -*- mode: python ; coding: utf-8 -*-
"""Configuracao do PyInstaller para o GPX Analisador de Rotas.

Empacota a GUI (app.py) num executavel Windows. A stack geoespacial
(pyproj, pyogrio, geopandas, osmnx, rtree, shapely) precisa que seus dados e
modulos ocultos sejam coletados explicitamente - feito abaixo com collect_all.

Gerar:   .venv\\Scripts\\pyinstaller.exe gpx_rotas.spec
Saida:   dist\\GPX_Analisador_Rotas\\GPX_Analisador_Rotas.exe

As pastas de dados (data/, output/) sao criadas em tempo de execucao ao lado do
.exe - nenhum caminho absoluto do computador de desenvolvimento e embutido.
"""
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = []
binaries = []
hiddenimports = []

# Pacotes com dados/binaries/submodulos que precisam ser coletados
for pkg in ("pyproj", "pyogrio", "geopandas", "osmnx", "shapely", "rtree",
            "folium", "branca", "customtkinter", "certifi"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += collect_submodules("sqlite3")
hiddenimports += ["gpxpy", "openpyxl", "networkx", "scipy",
                  "scipy._lib.array_api_compat.numpy.fft", "pandas"]

# Configuracoes padrao empacotadas (o app tambem procura ./config ao lado do exe)
datas += [("config/configuracoes.json", "config")]

a = Analysis(
    ["app.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "tkinter.test", "test", "pytest", "PyInstaller"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="GPX_Analisador_Rotas",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # aplicacao GUI (sem janela de console)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False, upx_exclude=[],
    name="GPX_Analisador_Rotas",
)
