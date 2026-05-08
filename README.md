# yt-strategy-engine

YouTube API verilerini gerçek zamanlı analiz ederek Claude API ile **içerik stratejisi** ve **12.000 karakterlik teknik senaryolar** üreten bir Python asistanı.

---

## Temel Prensipler

Bu proje, ürettiği her içerikte üç temel prensibe bağlıdır:

### 1. Teknik Doğruluk
Her bilgi, istatistik ve öneri **doğrulanmış, güncel ve pratik olarak uygulanabilir** olmalıdır. Kaynak gösterilir, somut verilerle desteklenir; spekülasyon yapılmaz. Teknik rehberler adım adım, eksiksiz ve test edilmiş içerik sunar.

### 2. Objektif Analiz
Veriler **önyargısız ve çok perspektiften** değerlendirilir. Güçlü yönler kadar gelişim alanları da dengeli biçimde ortaya konur. Kişisel görüşler, veri destekli çıkarımlardan net şekilde ayrılır. Rakip analizleri, gerçek YouTube metriklere dayanır.

### 3. Yapıcı Ton (Praising)
İzleyiciyi **motive eden, başarıları kutlayan ve ilham veren** bir dil kullanılır. Eleştiriler yapıcı çerçevelenir; her zorluk bir büyüme fırsatı olarak sunulur. Sıcak, samimi ve cesaretlendirici bir ses tonu kanalın markasını güçlendirir.

---

## Özellikler

| Özellik | Açıklama |
|---|---|
| **İçerik Stratejisi** | YouTube arama verisine dayalı 90 günlük yol haritası |
| **Senaryo Üretimi** | 12.000 karakterlik, yapılandırılmış teknik senaryolar |
| **Trend Analizi** | Bölgeye özgü trend video metrikleri |
| **Rakip Analizi** | Kanal karşılaştırması ve rekabet boşluğu tespiti |
| **SEO Optimizasyonu** | Veri destekli anahtar kelime ve etiket önerileri |
| **Prompt Caching** | Büyük bağlam blokları için Anthropic prompt cache |

---

## Kurulum

```bash
# Repoyu klonla
git clone https://github.com/aykutayalcinkaya-ops/yt-strategy-engine.git
cd yt-strategy-engine

# Sanal ortam oluştur ve aktifleştir
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Bağımlılıkları yükle
pip install -r requirements.txt

# .env dosyasını oluştur
cp .env.example .env
# .env dosyasını düzenle ve API anahtarlarını gir
```

---

## Yapılandırma

`.env` dosyası oluşturup aşağıdaki değişkenleri tanımla:

```env
ANTHROPIC_API_KEY=sk-ant-...
YOUTUBE_API_KEY=AIza...

# İsteğe bağlı
CLAUDE_MODEL=claude-sonnet-4-6
MAX_TOKENS=8192
YOUTUBE_MAX_RESULTS=50
YOUTUBE_REGION_CODE=TR
YOUTUBE_LANGUAGE=tr
OUTPUT_DIR=outputs
```

**YouTube API Anahtarı:** [Google Cloud Console](https://console.cloud.google.com/) → APIs & Services → YouTube Data API v3

**Anthropic API Anahtarı:** [Anthropic Console](https://console.anthropic.com/)

---

## Kullanım

### İçerik Stratejisi Üretme

```bash
# Temel strateji analizi
python main.py strategy "Python öğrenmek isteyenler için"

# Hedef kitle ve rakip analizi ile
python main.py strategy "veri bilimi" \
  --audience "yazılım mühendisleri" \
  --competitors "UCxxxxxx,UCyyyyyy" \
  --output outputs/strateji.md
```

### Teknik Senaryo Yazma

```bash
# 12.000 karakterlik senaryo üret
python main.py scenario \
  "Python ile web scraping" \
  "Python ile 10 Dakikada Web Scraping: Sıfırdan İleri Seviyeye" \
  --print

# Strateji analiziyle birlikte
python main.py scenario \
  "Docker nedir" \
  "Docker Öğreniyorum: Konteyner Teknolojisine Tam Rehber" \
  --with-strategy \
  --instructions "Başlangıç seviyesi için hazırla, terminal komutlarını vurgula"
```

### Trend Analizi

```bash
# Tüm kategorilerde trendler (Türkiye)
python main.py trending

# Teknoloji kategorisi (28)
python main.py trending --category 28
```

---

## Proje Yapısı

```
yt-strategy-engine/
├── src/
│   ├── __init__.py
│   ├── youtube_client.py     # YouTube Data API v3 entegrasyonu
│   ├── claude_client.py      # Anthropic Claude API + prompt caching
│   ├── strategy_engine.py    # İçerik stratejisi motoru
│   ├── scenario_generator.py # 12.000 karakter senaryo üretici
│   └── data_processor.py     # Veri toplama ve formatlama
├── outputs/                  # Üretilen senaryo ve stratejiler
├── data/                     # Yerel veri önbelleği
├── main.py                   # CLI giriş noktası
├── config.py                 # Merkezi yapılandırma (Pydantic)
├── requirements.txt
├── .env.example
└── .gitignore
```

---

## Teknik Mimari

```
Kullanıcı CLI
    │
    ▼
main.py (argparse CLI)
    │
    ├─► StrategyEngine
    │       ├─► YouTubeClient  →  YouTube Data API v3
    │       ├─► DataProcessor  →  İstatistik agregasyonu
    │       └─► ClaudeClient   →  Anthropic API (cache)
    │
    ├─► ScenarioGenerator
    │       └─► ClaudeClient   →  Anthropic API (streaming)
    │
    └─► YouTubeClient (doğrudan trend sorguları)
```

---

## Senaryo Yapısı

Üretilen her senaryo şu bölümleri içerir:

| Bölüm | Karakter |
|---|---|
| Intro Hook | 300–500 |
| Bağlam & Neden Önemli | 800–1.200 |
| Ana İçerik (4-6 alt bölüm) | 7.000–8.000 |
| Uzman İpuçları | 1.000–1.500 |
| Özet & Eylem Çağrısı | 500–800 |
| **TOPLAM** | **~12.000** |

---

## Bağımlılıklar

| Paket | Kullanım |
|---|---|
| `anthropic` | Claude API entegrasyonu, prompt caching |
| `google-api-python-client` | YouTube Data API v3 |
| `pydantic` | Yapılandırma doğrulama |
| `rich` | Terminal UI |
| `tenacity` | API çağrılarında otomatik yeniden deneme |
| `python-dotenv` | `.env` yükleme |

---

## Lisans

MIT License — Detaylar için `LICENSE` dosyasına bakın.
