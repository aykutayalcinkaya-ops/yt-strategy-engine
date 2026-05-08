from __future__ import annotations

from dataclasses import dataclass

from .claude_client import ClaudeClient
from .youtube_client import YouTubeClient, VideoMetrics
from .data_processor import DataProcessor


@dataclass
class ContentStrategy:
    topic: str
    target_audience: str
    recommended_formats: list[str]
    content_pillars: list[str]
    title_formulas: list[str]
    optimal_duration_minutes: int
    posting_frequency: str
    seo_keywords: list[str]
    competitive_gaps: list[str]
    raw_analysis: str


class StrategyEngine:
    def __init__(self):
        self._claude = ClaudeClient()
        self._youtube = YouTubeClient()
        self._processor = DataProcessor()

    def generate_strategy(
        self,
        topic: str,
        target_audience: str = "genel",
        competitor_channel_ids: list[str] = None,
    ) -> ContentStrategy:
        videos = self._youtube.search_videos(topic, order="viewCount")
        stats = self._processor.aggregate_video_stats(videos)
        top_tags = self._processor.extract_top_tags(videos)
        data_context = self._processor.format_stats_for_prompt(stats, topic)

        competitor_context = ""
        if competitor_channel_ids:
            channel_summaries = []
            for cid in competitor_channel_ids[:3]:
                ch = self._youtube.get_channel_metrics(cid)
                if ch:
                    channel_summaries.append(self._processor.format_channel_for_prompt(ch))
            competitor_context = "\n".join(channel_summaries)

        prompt = f"""\
Konu: {topic}
Hedef Kitle: {target_audience}

{data_context}

{f"### Rakip Kanal Verileri{chr(10)}{competitor_context}" if competitor_context else ""}

Popüler etiketler: {", ".join(top_tags[:15])}

Bu verilere dayanarak kapsamlı bir YouTube içerik stratejisi oluştur. Yanıtını şu başlıklarda yapılandır:

1. **Hedef Kitle Analizi** — Kim için içerik üretiyoruz? Demografik ve psikografik profil.
2. **İçerik Sütunları** — Kanalın odaklanması gereken 4-6 ana tema.
3. **Önerilen Format Türleri** — Tutorial, vlog, liste, analiz vb. ve her biri için neden.
4. **Başlık Formülleri** — Tıklama oranını artıracak 5 kanıtlanmış başlık şablonu.
5. **Optimal Yayın Stratejisi** — Süre, sıklık ve zamanlama önerileri.
6. **SEO & Keşfedilebilirlik** — Öncelikli anahtar kelimeler ve meta strateji.
7. **Rekabetçi Boşluklar** — Rakiplerin karşılamadığı fırsatlar.
8. **90 Günlük Yol Haritası** — Uygulanabilir, adım adım eylem planı.

Her öneriyi veriyle destekle. Yapıcı ve motive edici bir ton kullan.
"""

        raw = self._claude.complete_with_cache(
            prompt=prompt,
            cached_context=data_context,
            max_tokens=4096,
        )

        return ContentStrategy(
            topic=topic,
            target_audience=target_audience,
            recommended_formats=self._extract_list(raw, "Format Türleri"),
            content_pillars=self._extract_list(raw, "İçerik Sütunları"),
            title_formulas=self._extract_list(raw, "Başlık Formülleri"),
            optimal_duration_minutes=stats.get("avg_duration_minutes", 10),
            posting_frequency="Haftada 2-3 video",
            seo_keywords=top_tags[:10],
            competitive_gaps=self._extract_list(raw, "Rekabetçi Boşluklar"),
            raw_analysis=raw,
        )

    def _extract_list(self, text: str, section_hint: str) -> list[str]:
        lines = []
        in_section = False
        for line in text.splitlines():
            if section_hint.lower() in line.lower():
                in_section = True
                continue
            if in_section:
                stripped = line.strip()
                if stripped.startswith(("##", "**")) and section_hint.lower() not in stripped.lower():
                    break
                if stripped.startswith(("-", "*", "•")) or (len(stripped) > 2 and stripped[0].isdigit()):
                    clean = stripped.lstrip("-*•0123456789. ").strip()
                    if clean:
                        lines.append(clean)
        return lines[:8]
