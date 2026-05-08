from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime

from .claude_client import ClaudeClient
from .strategy_engine import ContentStrategy


SCENARIO_SYSTEM_PROMPT = """\
Sen, YouTube için 12.000 karakterlik teknik senaryolar yazan uzman bir içerik yazarısın.

Senaryoların üç temel prensibe uygun olması zorunludur:

### Teknik Doğruluk
- Tüm teknik bilgiler doğrulanmış ve güncel olmalıdır.
- İstatistikler, kaynaklara atıfla sunulmalıdır.
- Adım adım talimatlar eksiksiz ve doğru olmalıdır.

### Objektif Analiz
- Konuyu birden fazla perspektiften ele al.
- Avantaj ve dezavantajları dengeli biçimde sun.
- Kişisel görüşleri açıkça işaretle.

### Yapıcı Ton (Praising)
- İzleyicileri cesaretlendir ve motive et.
- Başarıları ve ilerlemeyi kutla.
- Her zorluğu öğrenme fırsatına dönüştür.
- Sıcak, samimi ve ilham verici bir dil kullan.

SENARYO UZUNLUĞU: Tam olarak 11.000-13.000 karakter arasında olmalıdır.
"""

SCENARIO_STRUCTURE = """\
Aşağıdaki yapıyı kullanarak teknik senaryoyu oluştur:

1. **INTRO HOOK** (300-500 karakter)
   - Güçlü, merak uyandıran açılış cümlesi
   - İzleyicinin kazanımını hemen belirt
   - Kanalı tanıt ve abone olmaya davet et

2. **BAĞLAM & NEDEN ÖNEMLİ** (800-1200 karakter)
   - Konunun neden önemli olduğunu açıkla
   - İzleyicinin acı noktasını (pain point) doğrula
   - Çözümün genel çerçevesini çiz

3. **ANA İÇERİK BÖLÜMÜ** (7000-8000 karakter)
   - 4-6 alt bölüme böl
   - Her bölümde: açıklama → teknik detay → pratik örnek
   - Gerçek hayat senaryoları ve vaka çalışmaları ekle
   - Adım adım talimatlar numaralandır

4. **UZMAN İPUÇLARI** (1000-1500 karakter)
   - 5-7 ileri düzey ipucu
   - Yaygın hatalar ve nasıl önleneceği
   - Performans optimizasyonu önerileri

5. **ÖZET & EYLEM ÇAĞRISI** (500-800 karakter)
   - Ana noktaları özetle
   - Bir sonraki adımı net belirt
   - Like, yorum ve abone çağrısı
   - Gelecek video teaserı
"""


@dataclass
class Scenario:
    topic: str
    title: str
    content: str
    char_count: int
    word_count: int
    created_at: str
    strategy_context: str = ""

    @property
    def meets_length_requirement(self) -> bool:
        return 11000 <= self.char_count <= 13000


class ScenarioGenerator:
    def __init__(self):
        self._claude = ClaudeClient()

    def generate(
        self,
        topic: str,
        title: str,
        strategy: ContentStrategy = None,
        custom_instructions: str = "",
    ) -> Scenario:
        strategy_context = ""
        if strategy:
            strategy_context = f"""\
## Strateji Bağlamı
- Hedef kitle: {strategy.target_audience}
- İçerik sütunları: {", ".join(strategy.content_pillars[:3])}
- SEO anahtar kelimeleri: {", ".join(strategy.seo_keywords[:8])}
"""

        prompt = f"""\
{strategy_context}

## Video Başlığı
{title}

## Konu
{topic}

{f"## Özel Talimatlar{chr(10)}{custom_instructions}" if custom_instructions else ""}

{SCENARIO_STRUCTURE}

ÖNEMLİ: Senaryo tam olarak 11.000 ile 13.000 karakter arasında olmalıdır.
Konuşma diline uygun, doğal ve akıcı bir Türkçe kullan.
Teknik terimler için parantez içinde kısa açıklama ekle.
"""

        content = self._claude.complete(
            prompt=prompt,
            system=SCENARIO_SYSTEM_PROMPT,
            max_tokens=8192,
        )

        # If the scenario is too short, expand it
        if len(content) < 11000:
            content = self._expand_scenario(content, topic, title, 13000 - len(content))

        return Scenario(
            topic=topic,
            title=title,
            content=content,
            char_count=len(content),
            word_count=len(content.split()),
            created_at=datetime.now().isoformat(),
            strategy_context=strategy_context,
        )

    def _expand_scenario(
        self, existing: str, topic: str, title: str, chars_needed: int
    ) -> str:
        prompt = f"""\
Aşağıdaki YouTube senaryosu çok kısa. Yaklaşık {chars_needed} karakter daha ekleyerek
genişlet. Mevcut yapıyı koru, ancak her bölüme daha fazla teknik detay, örnek ve açıklama ekle.
Başlık: {title}
Konu: {topic}

MEVCUT SENARYO:
{existing}

GENİŞLETİLMİŞ VERSİYON (tüm metni eksiksiz yaz):
"""
        return self._claude.complete(prompt=prompt, system=SCENARIO_SYSTEM_PROMPT, max_tokens=8192)

    def save(self, scenario: Scenario, output_dir: str = "outputs") -> str:
        os.makedirs(output_dir, exist_ok=True)
        safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in scenario.title)[:50]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{output_dir}/{timestamp}_{safe_title}.txt"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(f"BAŞLIK: {scenario.title}\n")
            f.write(f"KONU: {scenario.topic}\n")
            f.write(f"KARAKTER SAYISI: {scenario.char_count:,}\n")
            f.write(f"KELİME SAYISI: {scenario.word_count:,}\n")
            f.write(f"OLUŞTURULMA: {scenario.created_at}\n")
            f.write(f"UZUNLUK GEREKSİNİMİ: {'✓ KARŞILANDI' if scenario.meets_length_requirement else '✗ KARŞILANMADI'}\n")
            f.write("\n" + "=" * 80 + "\n\n")
            f.write(scenario.content)
        return filename
