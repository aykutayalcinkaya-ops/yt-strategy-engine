# yt-manual-analyzer

YouTube Studio'dan manuel olarak dışa aktarılan CSV/Excel dosyalarını analiz eden,
Claude API ile **teknik senaryo** ve **içerik stratejisi** üreten bir Python asistanı.

> **Temel fark:** YouTube API'sine gerek yok. Kendi kanalının verilerini
> YouTube Studio'dan indirip `Analizler/` klasörüne koyuyorsun; sistem geri kalanını hallediyor.

---

## Temel Prensipler

### 1. Teknik Doğruluk
Her bilgi, istatistik ve öneri **doğrulanmış, güncel ve pratik olarak uygulanabilir** olmalıdır.
Kaynak gösterilir, somut verilerle desteklenir; spekülasyon yapılmaz.
Fizik, borsa ve tarih içeriklerinde alan doğrulaması otomatik olarak çalışır.

### 2. Objektif Analiz
Veriler **önyargısız ve çok perspektiften** değerlendirilir.
Güçlü yönler kadar gelişim alanları da dengeli biçimde ortaya konur.
Kişisel görüşler, veri destekli çıkarımlardan net şekilde ayrılır.

### 3. Yapıcı Ton (Praising)
İzleyiciyi **motive eden, başarıları kutlayan ve ilham veren** bir dil kullanılır.
Eleştiriler yapıcı çerçevelenir; her zorluk bir büyüme fırsatı olarak sunulur.

---

## Özellikler

| Özellik | Açıklama |
|---|---|
| **Manuel Veri Analizi** | CSV/Excel → izlenme, CTR, izleme süresi özeti |
| **Shorts vs Long-form** | Format karşılaştırması ve öneri |
| **Teknik Senaryo Üretimi** | 3-pas doğrulamalı 12.000+ karakter senaryo |
| **Alan Doğrulaması** | Fizik, borsa, tarih iddialarını otomatik kontrol |
| **Düzeltme Listesi** | Hangi videolar teknik iyileştirme gerektiriyor |
| **Konu Önerileri** | Veriden damıtılmış video fikir listesi |
| **Prompt Caching** | Büyük bağlam blokları için Anthropic prompt cache |

---

## Kurulum

```bash
# Repoyu klonla
git clone https://github.com/aykutayalcinkaya-ops/yt-manual-analyzer.git
cd yt-manual-analyzer

# Sanal ortam oluştur ve aktifleştir
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# Bağımlılıkları yükle
pip install -r requirements.txt

# .env dosyasını oluştur
cp .env.example .env
# .env içine API anahtarlarını gir (bkz. Yapılandırma)
```

---

## Yapılandırma

`.env` dosyasına aşağıdaki değişkenleri ekle:

```env
# Zorunlu
ANTHROPIC_API_KEY=sk-ant-...

# İsteğe bağlı (YouTube API özelliklerini kullanmak istersen)
YOUTUBE_API_KEY=AIza...

# Claude ayarları
CLAUDE_MODEL=claude-sonnet-4-6
MAX_TOKENS=8192
SCENARIO_MAX_TOKENS=16000

# Çıktı
OUTPUT_DIR=outputs
```

**Anthropic API Anahtarı:** [console.anthropic.com](https://console.anthropic.com) → API Keys → Create Key

---

## Manuel Veri Analizi — Hızlı Başlangıç

### 1. YouTube Studio'dan veri indir

1. [YouTube Studio](https://studio.youtube.com) → **Analitik** → **Gelişmiş Mod**
2. Tarih aralığını seç
3. Sağ üstte **İndir (↓)** → **CSV olarak indir**
4. Dosyayı `Analizler/` klasörüne kopyala

### 2. Analizi çalıştır

```bash
# Tek komutla: Veri oku → Analiz et → Senaryo yaz
python main.py pipeline "içerik konusu"

# Sadece dosya analizi
python main.py analyze-files

# Senaryo üretimi (manuel veri bağlamıyla)
python main.py create "kuantum fiziği" --title "Kuantum Mekaniği Nedir?"
```

### 3. Çıktılar

```
outputs/
└── senaryolar/
    └── 2026-05-11_1430_kuantum_mekanigi_nedir.md
```

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

## Proje Yapısı

```
yt-manual-analyzer/
├── Analizler/                    ← YouTube Studio CSV/Excel dosyaları buraya
│   └── ORNEK_FORMAT.csv
├── src/
│   ├── __init__.py
│   ├── file_processor.py         ← Manuel veri okuma ve özetleme
│   ├── content_creator.py        ← 3-pas doğrulamalı senaryo üretici
│   ├── processor.py              ← Algoritmik analiz (VideoProcessor)
│   ├── ai_handler.py             ← Claude API — kanal/video/yorum analizi
│   ├── youtube_client.py         ← YouTube Data API v3 (opsiyonel)
│   ├── claude_client.py          ← Anthropic API istemcisi
│   ├── strategy_engine.py        ← İçerik stratejisi motoru
│   ├── scenario_generator.py     ← Temel senaryo üretici
│   └── data_processor.py         ← İstatistik agregasyonu
├── outputs/
│   └── senaryolar/               ← Üretilen Markdown senaryolar
├── main.py                       ← CLI giriş noktası
├── config.py                     ← Pydantic yapılandırma
├── requirements.txt
├── .env.example
└── .gitignore
```

---

## Senaryo Yapısı

Üretilen her senaryo dört zorunlu bölümden oluşur:

| Bölüm | Hedef Karakter |
|---|---|
| Giriş | 400–700 |
| Derinlemesine Analiz | 4.500–6.000 |
| Teknik Detaylar | 3.500–5.000 |
| Yapıcı Sonuç | 600–1.000 |
| **Toplam** | **≥ 12.000** |

Senaryo otomatik olarak konuyu tarar; **fizik**, **borsa** veya **tarih**
içeriği tespit edilirse alan doğrulaması ek bir pas olarak çalışır.

---

## Bağımlılıklar

| Paket | Kullanım |
|---|---|
| `anthropic` | Claude API, prompt caching, streaming |
| `pandas` | CSV/Excel okuma, veri temizleme, özetleme |
| `openpyxl` | Excel (.xlsx) desteği |
| `pydantic` | Yapılandırma doğrulama |
| `rich` | Terminal UI (tablolar, paneller, ilerleme) |
| `tenacity` | API çağrılarında otomatik yeniden deneme |
| `python-dotenv` | `.env` dosyası yükleme |
| `google-api-python-client` | YouTube Data API v3 (opsiyonel) |

---

## Lisans

MIT License — Detaylar için `LICENSE` dosyasına bakın.
