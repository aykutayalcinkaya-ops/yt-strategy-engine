"""
runtime_hooks/set_workdir.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
PyInstaller runtime hook:
  EXE olarak çalışırken çalışma dizinini EXE'nin bulunduğu klasöre
  taşır; böylece Analizler/, outputs/ ve .env dosyaları EXE'nin
  yanında aranır ve oluşturulur (sys._MEIPASS geçici klasöründe değil).
"""

import os
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    exe_dir = Path(sys.executable).resolve().parent
    os.chdir(exe_dir)

    # Kullanıcı verisi için gerekli klasörleri oluştur
    for folder in ("Analizler", "outputs/scripts", "outputs/senaryolar"):
        (exe_dir / folder).mkdir(parents=True, exist_ok=True)
