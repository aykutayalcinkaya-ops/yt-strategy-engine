# yt-manual-analyzer

YouTube Studio'dan indirilen CSV/Excel verilerini analiz eden, Claude API ile
**teknik senaryo** ve **içerik stratejisi** üreten Python asistanı.

> **YouTube API gerekmez.** Kanalının verilerini YouTube Studio'dan indirip
> `Analizler/` klasörüne koy; sistem geri kalanını halleder.

---

## Hızlı Başlangıç

```bash
# 1. Repoyu klonla
git clone https://github.com/aykutayalcinkaya-ops/yt-strategy-engine.git
cd yt-strategy-engine

# 2. Sanal ortam oluştur ve aktifleştir
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Bağımlılıkları yükle
pip install -r requirements.txt

# 4. API anahtarını ayarla
cp .env.example .env
# .env dosyasını aç ve ANTHROPIC_API_KEY değerini gir

# 5. Çalıştır
python main.py
```

---

## Kurulum — Adım Adım

### Gereksinimler

| Gereksinim | Sürüm | Notlar |
|---|---|---|
| Python | 3.10+ | 3.11 önerilir |
| Anthropic API anahtarı | — | **Zorunlu** |
| YouTube API anahtarı | — | İsteğe bağlı |

### 1. API Anahtarı Al

**Anthropic (zorunlu):**
1. [console.anthropic.com](https://console.anthropic.com) → API Keys → Create Key
2. `sk-ant-...` ile başlayan anahtarı kopyala

**YouTube Data API v3 (isteğe bağlı — trend analizi için):**
1. [console.cloud.google.com](https://console.cloud.google.com) → APIs & Services
2. YouTube Data API v3'ü etkinleştir → Credentials → Create API Key

### 2. `.env` Dosyasını Oluştur

```bash
cp .env.example .env
```

`.env` dosyasını aç ve değerleri doldur:

```env
# Zorunlu
ANTHROPIC_API_KEY=sk-ant-...

# Claude model seçimi
CLAUDE_MODEL=claude-sonnet-4-6   # veya claude-opus-4-7
MAX_TOKENS=8192
SCENARIO_MAX_TOKENS=16000

# Çıktı klasörü
OUTPUT_DIR=outputs
```

### 3. YouTube Studio'dan Veri İndir

1. [studio.youtube.com](https://studio.youtube.com) → **Analitik** → **Gelişmiş Mod**
2. Tarih aralığını seç → sağ üstte **İndir (↓)** → **CSV olarak indir**
3. İndirilen dosyayı `Analizler/` klasörüne kopyala

---

## Kullanım

### İnteraktif Mod (Önerilen)

```bash
python main.py
```

Program başladığında:
1. `Analizler/` klasöründe yeni dosya tarar
2. İstatistik raporunu ve AI içgörülerini ekrana basar
3. `"Bu verilere göre yeni bir video senaryosu hazırlamamı ister misin?"` diye sorar
4. Onay gelirse 12.000+ karakterlik doğrulamalı senaryo üretir ve `outputs/scripts/` altına kaydeder

### Grafik Arayüz

```bash
python main.py gui
```

Sol menüden sekme seç:

| Sekme | Ne Yapar |
|---|---|
| **Veri Analizi** | CSV/Excel yükle → FileProcessor + AIStrategist analizi |
| **Senaryo Yazarı** | Konu gir → 4-pas WriterEngine pipeline → kaydet/kopyala |
| **Ayarlar** | API anahtarlarını doğrudan arayüzden yönet, `.env`'e yaz |

### Komut Satırı

```bash
# Sadece analiz (senaryo sormaz)
python main.py scan

# Tüm dosyaları yeniden işle
python main.py scan --force

# Belirli bir konuda senaryo üret
python main.py create "kuantum fiziği" --title "Kuantum Mekaniği Nedir?"

# Ek yönergelerle senaryo
python main.py create "yapay zeka" --instructions "Türkiye'deki uygulamalara odaklan"

# YouTube API ile içerik stratejisi (YOUTUBE_API_KEY gerekli)
python main.py strategy "Python programlama" --audience "yeni başlayanlar"

# Trend video analizi (YOUTUBE_API_KEY gerekli)
python main.py trending --category 28    # 28 = Bilim & Teknoloji
```

---

## Özellikler

| Özellik | Açıklama |
|---|---|
| **Manuel Veri Analizi** | CSV/Excel → izlenme, CTR, izleme süresi özeti |
| **AI Strateji Analizi** | "Hangi içerik türüne odaklanmalıyım?" sorusunu yanıtlar |
| **4-Pas Senaryo Pipeline** | Taslak → İddia çıkarımı → Çapraz doğrulama → Düzeltme |
| **Alan Doğrulaması** | Fizik, finans, tarih iddialarını otomatik kontrol |
| **Shorts vs Long-form** | Format karşılaştırması ve öneri |
| **Grafik Arayüz** | customtkinter koyu tema, thread-safe, donmayan UI |
| **EXE Derleme** | PyInstaller ile tek dosya Windows uygulaması |
| **Prompt Caching** | Büyük bağlamlar için Anthropic prompt cache |

---

## Proje Yapısı

```
yt-manual-analyzer/
├── .env.example              ← API anahtar şablonu (bunu kopyala → .env)
├── .github/
│   └── workflows/ci.yml      ← GitHub Actions CI
├── Analizler/
│   └── ORNEK_FORMAT.csv      ← Beklenen CSV formatı örneği
├── assets/
│   ├── icon.ico              ← EXE ikonu (16–256 px)
│   └── create_icon.py        ← İkonu yeniden oluşturma betiği
├── outputs/
│   ├── scripts/              ← WriterEngine senaryo çıktıları
│   └── senaryolar/           ← ContentCreator senaryo çıktıları
├── runtime_hooks/
│   └── set_workdir.py        ← PyInstaller EXE için çalışma dizini ayarı
├── scripts/
│   ├── build.bat             ← Windows EXE derleme betiği
│   └── build.sh              ← macOS/Linux EXE derleme betiği
├── src/
│   ├── file_processor.py     ← CSV/Excel okuma ve özetleme
│   ├── ai_strategist.py      ← Claude ile strateji analizi
│   ├── writer_engine.py      ← 4-pas doğrulamalı senaryo üretici
│   ├── gui_app.py            ← customtkinter grafik arayüz
│   ├── claude_client.py      ← Anthropic API istemcisi
│   ├── ai_handler.py         ← Kanal/video/yorum analizi
│   ├── youtube_client.py     ← YouTube Data API v3
│   ├── content_creator.py    ← 3-pas senaryo üretici (alternatif)
│   ├── processor.py          ← Algoritmik video analizi
│   ├── strategy_engine.py    ← İçerik stratejisi motoru
│   ├── scenario_generator.py ← Temel senaryo üretici
│   └── data_processor.py     ← İstatistik agregasyonu
├── build_config.spec         ← PyInstaller yapılandırması
├── config.py                 ← Pydantic yapılandırma modeli
├── main.py                   ← CLI + GUI giriş noktası
├── requirements.txt          ← Runtime bağımlılıkları
└── requirements-build.txt    ← EXE derlemesi için ek paketler
```

---

## Senaryo Pipeline'ı

```
Pas 1 — Taslak           Claude 12.000+ karakter üretir
Pas 2 — İddia Çıkarımı   Fizik/finans/tarih iddiaları listelenir
Pas 3 — Doğrulama        Alan uzmanı promptlarıyla çapraz kontrol
Pas 4 — Düzeltme         Hatalar giderilir, kısa bölümler genişletilir
```

| Bölüm | Hedef Karakter |
|---|---|
| HOOK | 300–600 |
| BAĞLAM | 800–1.400 |
| BÖLÜM_1 | 1.800–2.800 |
| BÖLÜM_2 | 1.800–2.800 |
| BÖLÜM_3 | 1.500–2.400 |
| UZMAN | 900–1.600 |
| KAPANIS | 600–1.000 |
| **Toplam** | **≥ 12.000** |

---

## EXE Derleme (Windows)

```bash
# 1. Ek bağımlılıkları yükle
pip install -r requirements-build.txt

# 2. Derle
scripts\build.bat        # Windows
bash scripts/build.sh    # macOS / Linux
```

Çıktı: `dist/yt-manual-analyzer.exe`

EXE'nin yanına koy:
```
.env          ← ANTHROPIC_API_KEY=sk-ant-...
Analizler/    ← YouTube Studio CSV dosyaları (klasör otomatik oluşur)
```

---

## Temel Prensipler

Her AI çıktısı üç prensiple üretilir:

**1. Teknik Doğruluk** — Her bilgi, istatistik ve öneri doğrulanmış, güncel
ve pratik olarak uygulanabilir olmalıdır. Fizik, finans ve tarih içeriklerinde
alan doğrulaması otomatik çalışır.

**2. Objektif Analiz** — Veriler önyargısız ve çok perspektiften
değerlendirilir. Güçlü yönler kadar gelişim alanları da dengeli biçimde ortaya
konur.

**3. Yapıcı Ton (Praising)** — İzleyiciyi motive eden, başarıları kutlayan
ve ilham veren bir dil kullanılır. Her zorluk bir büyüme fırsatı olarak
sunulur.

---

## Desteklenen Sütun Adları

`FileProcessor` aşağıdaki YouTube Studio TR/EN sütun adlarını otomatik tanır:

| Standart Ad | Türkçe Varyantlar | İngilizce Varyantlar |
|---|---|---|
| `title` | Video başlığı, İçerik | Video title, Content |
| `views` | İzlenme sayısı, İzlenmeler | Views, View count |
| `ctr` | Tıklama oranı (TO), TO (%) | CTR (%), Click-through rate |
| `avg_watch_duration` | Ortalama izleme süresi | Average view duration |
| `watch_time_hours` | İzleme süresi (saat) | Watch time (hours) |
| `subscribers_gained` | Abone kazanımı | Subscribers gained |
| `published_at` | Yayınlanma tarihi | Publish date |

---

## Lisans

MIT License — detaylar için `LICENSE` dosyasına bakın.
