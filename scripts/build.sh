#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────────────
#  scripts/build.sh  —  macOS / Linux derleme betiği
#  Çalıştır: bash scripts/build.sh
# ────────────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")/.."

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

echo ""
echo "  yt-manual-analyzer  |  Derleme Betiği"
echo "  ════════════════════════════════════════"
echo ""

# ── 1. Paket kontrolü ────────────────────────────────────────────── #
echo "  [1/4] Bağımlılıklar kontrol ediliyor…"
pip install pyinstaller pillow customtkinter --quiet

# ── 2. İkon oluştur ──────────────────────────────────────────────── #
echo "  [2/4] İkon oluşturuluyor…"
python assets/create_icon.py || echo -e "  ${YELLOW}Uyarı: ikon oluşturulamadı, devam ediliyor.${NC}"

# ── 3. Temizlik ───────────────────────────────────────────────────── #
echo "  [3/4] Eski derleme temizleniyor…"
rm -rf build/
rm -f  dist/yt-manual-analyzer dist/yt-manual-analyzer.exe

# ── 4. Derleme ───────────────────────────────────────────────────── #
echo "  [4/4] PyInstaller çalışıyor…  (2-5 dakika sürebilir)"
pyinstaller build_config.spec --noconfirm --clean

echo ""
echo "  ════════════════════════════════════════"
echo -e "  ${GREEN}BAŞARILI!${NC}  dist/yt-manual-analyzer"
echo ""
echo "  Çalıştırmadan önce EXE/binary yanına kopyalayın:"
echo "    • .env          (ANTHROPIC_API_KEY=sk-ant-...)"
echo "    • Analizler/    (YouTube Studio CSV dosyaları)"
echo "  ════════════════════════════════════════"
echo ""
