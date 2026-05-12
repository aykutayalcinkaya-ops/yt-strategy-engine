# -*- mode: python ; coding: utf-8 -*-
# ────────────────────────────────────────────────────────────────────────────
# build_config.spec  —  yt-manual-analyzer  PyInstaller derleme yapılandırması
#
# Derleme:
#   pip install pyinstaller pillow customtkinter
#   python assets/create_icon.py          # ikon oluştur (bir kez yeter)
#   pyinstaller build_config.spec
#
# Çıktı: dist/yt-manual-analyzer[.exe]
#
# Kullanım (EXE yanında şu dosyalar/klasörler olmalıdır):
#   Analizler/    ← YouTube Studio CSV/Excel dosyaları buraya
#   .env          ← ANTHROPIC_API_KEY=sk-ant-...  (kullanıcı oluşturur)
#   outputs/      ← otomatik oluşturulur
# ────────────────────────────────────────────────────────────────────────────

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

# ── Proje kök dizini ────────────────────────────────────────────────────── #
ROOT = Path(SPECPATH)               # .spec dosyasının bulunduğu dizin

# ── customtkinter tema / font varlıkları ────────────────────────────────── #
ctk_datas = collect_data_files("customtkinter")

# ── Derlemeye dahil edilecek veri dosyaları ─────────────────────────────── #
added_datas = [
    # customtkinter: dark-blue, blue, green tema JSON'ları + font dosyaları
    *ctk_datas,

    # Örnek veri klasörü (kullanıcıya format göstermek için)
    ("Analizler/ORNEK_FORMAT.csv", "Analizler"),

    # Kurulum kılavuzu
    (".env.example", "."),
    ("README.md",    "."),
]

# ── Gizli importlar ─────────────────────────────────────────────────────── #
# PyInstaller dinamik importları otomatik bulamaz; buraya ekliyoruz.
hidden = [
    # ── Tkinter & customtkinter ──────────────────────────────── #
    "tkinter",
    "tkinter.filedialog",
    "tkinter.messagebox",
    "tkinter.ttk",
    "_tkinter",
    # customtkinter alt modülleri (tkinter olmadan collect_submodules çalışmaz)
    "customtkinter",
    "customtkinter.windows",
    "customtkinter.windows.ctk_input_dialog",
    "customtkinter.windows.ctk_tk",
    "customtkinter.windows.ctk_toplevel",
    "customtkinter.windows.widgets",
    "customtkinter.windows.widgets.appearance_mode",
    "customtkinter.windows.widgets.appearance_mode.appearance_mode_base_class",
    "customtkinter.windows.widgets.appearance_mode.appearance_mode_tracker",
    "customtkinter.windows.widgets.core_rendering",
    "customtkinter.windows.widgets.core_rendering.ctk_canvas",
    "customtkinter.windows.widgets.core_rendering.draw_engine",
    "customtkinter.windows.widgets.core_widget_classes",
    "customtkinter.windows.widgets.core_widget_classes.ctk_base_class",
    "customtkinter.windows.widgets.core_widget_classes.dropdown_menu",
    "customtkinter.windows.widgets.ctk_button",
    "customtkinter.windows.widgets.ctk_checkbox",
    "customtkinter.windows.widgets.ctk_combobox",
    "customtkinter.windows.widgets.ctk_entry",
    "customtkinter.windows.widgets.ctk_frame",
    "customtkinter.windows.widgets.ctk_label",
    "customtkinter.windows.widgets.ctk_optionmenu",
    "customtkinter.windows.widgets.ctk_progressbar",
    "customtkinter.windows.widgets.ctk_radiobutton",
    "customtkinter.windows.widgets.ctk_scrollable_frame",
    "customtkinter.windows.widgets.ctk_scrollbar",
    "customtkinter.windows.widgets.ctk_segmented_button",
    "customtkinter.windows.widgets.ctk_slider",
    "customtkinter.windows.widgets.ctk_switch",
    "customtkinter.windows.widgets.ctk_tabview",
    "customtkinter.windows.widgets.ctk_textbox",
    "customtkinter.windows.widgets.font",
    "customtkinter.windows.widgets.scaling",
    "customtkinter.windows.widgets.theme",
    "customtkinter.windows.widgets.utility",

    # ── Proje modülleri (src/) ───────────────────────────────── #
    "src",
    "src.gui_app",
    "src.file_processor",
    "src.ai_strategist",
    "src.writer_engine",
    "src.claude_client",
    "src.ai_handler",
    "src.content_creator",
    "src.processor",
    "src.youtube_client",
    "src.strategy_engine",
    "src.scenario_generator",
    "src.data_processor",

    # ── Anthropic SDK ────────────────────────────────────────── #
    "anthropic",
    "anthropic._legacy_response",
    "anthropic._models",
    "anthropic._streaming",
    "anthropic.types",
    "anthropic.resources",
    "httpx",
    "httpcore",
    "certifi",
    "anyio",
    "sniffio",

    # ── Pandas / Excel ───────────────────────────────────────── #
    "pandas",
    "pandas.io.formats.style",
    "openpyxl",
    "openpyxl.styles",
    "openpyxl.utils",
    "xlrd",

    # ── Diğer bağımlılıklar ──────────────────────────────────── #
    "pydantic",
    "pydantic.v1",
    "dotenv",
    "rich",
    "rich.console",
    "rich.panel",
    "rich.table",
    "rich.rule",
    "rich.prompt",
    "tenacity",

    # ── Python stdlib (PyInstaller bazen atlar) ──────────────── #
    "json",
    "queue",
    "threading",
    "pathlib",
    "textwrap",
    "traceback",
    "datetime",
    "re",
    "os",
    "sys",
]

# ── Derleme dışı bırakılanlar (boyutu küçültür) ─────────────────────────── #
excludes = [
    "matplotlib",
    "numpy",
    "scipy",
    "IPython",
    "jupyter",
    "notebook",
    "PIL",               # yalnızca ikon üretimi için kullanıldı; runtime'da gerekmez
    "pytest",
    "setuptools",
    "pkg_resources",
    "docutils",
    "sphinx",
    "google.cloud",
    "google.oauth2",     # YouTube API (opsiyonel) — kullanmak isteyenler manuel ekler
    "googleapiclient",
]

block_cipher = None

# ── Analiz ──────────────────────────────────────────────────────────────── #
a = Analysis(
    ["main.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=added_datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=["runtime_hooks/set_workdir.py"],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── EXE (--onefile modu) ─────────────────────────────────────────────────── #
# Tüm binary/data doğrudan EXE'ye gömülür (COLLECT kullanılmaz → tek dosya)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,          # ← onefile: binary'ler EXE içinde
    a.zipfiles,          # ← onefile: zip verileri EXE içinde
    a.datas,             # ← onefile: data dosyaları EXE içinde
    [],
    name="yt-manual-analyzer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,            # UPX kuruluysa sıkıştırır (opsiyonel)
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,       # --noconsole: siyah terminal penceresi açılmaz
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
)

# NOT: COLLECT() çağrısı kasıtlı olarak yok → --onefile davranışı
