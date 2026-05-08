from __future__ import annotations

import os
from dataclasses import dataclass, field
from statistics import mean
from textwrap import dedent
from typing import Iterator

import anthropic
from tenacity import retry, stop_after_attempt, wait_exponential

from .youtube_client import ChannelMetrics, CommentData, VideoMetrics


# ------------------------------------------------------------------ #
# Sistem promptu — üç temel prensip                                    #
# ------------------------------------------------------------------ #

_SYSTEM_PROMPT = dedent("""\
    Sen, YouTube kanallarını analiz eden ve içerik stratejisi geliştiren uzman bir asistansın.
    Yanıtlarında üç temel prensibe kesinlikle uyarsın:

    **Teknik Doğruluk:** Her istatistik ve öneri sağlanan ham veriye dayanır.
    Sayıları yanlış yansıtma; kaynak verisi yoksa tahmin olduğunu belirt.

    **Objektif Analiz:** Güçlü ve zayıf yönleri dengeli biçimde sun.
    Önyargı taşıma; birden fazla perspektifi göz önünde bulundur.

    **Yapıcı Ton (Praising):** Eleştirileri fırsat olarak çerçevele.
    Başarıları kutla, izleyiciyi ve içerik üreticisini motive et.
""")


# ------------------------------------------------------------------ #
# Yanıt veri yapıları                                                  #
# ------------------------------------------------------------------ #

@dataclass
class ChannelAuditResult:
    channel_title: str
    strengths: list[str]
    improvement_areas: list[str]
    content_gaps: list[str]
    recommended_actions: list[str]
    raw_response: str


@dataclass
class VideoAnalysisResult:
    video_title: str
    performance_verdict: str
    engagement_insights: list[str]
    audience_sentiment: str
    optimization_tips: list[str]
    raw_response: str


@dataclass
class ContentRecommendation:
    topic_ideas: list[str]
    title_formulas: list[str]
    optimal_duration_minutes: int
    best_posting_time: str
    seo_keywords: list[str]
    raw_response: str


@dataclass
class CommentSummary:
    overall_sentiment: str          # positive / negative / mixed
    top_themes: list[str]
    frequently_asked_questions: list[str]
    praise_highlights: list[str]
    criticism_highlights: list[str]
    raw_response: str


# ------------------------------------------------------------------ #
# Prompt bağlamı oluşturucu                                            #
# ------------------------------------------------------------------ #

class PromptContextBuilder:
    """Ham YouTube verilerini Claude'a gönderilecek yapılandırılmış metne dönüştürür."""

    @staticmethod
    def channel_context(
        channel: ChannelMetrics,
        recent_videos: list[VideoMetrics],
    ) -> str:
        avg_views = round(mean(v.view_count for v in recent_videos)) if recent_videos else 0
        avg_eng = round(mean(v.engagement_rate for v in recent_videos), 2) if recent_videos else 0
        top5 = sorted(recent_videos, key=lambda v: v.view_count, reverse=True)[:5]

        top5_lines = "\n".join(
            f"  {i+1}. {v.title}\n"
            f"     İzlenme: {v.view_count:,} | Etkileşim: %{v.engagement_rate} | "
            f"Süre: {v.duration_minutes} dk | Tarih: {v.published_at[:10]}"
            for i, v in enumerate(top5)
        )

        return dedent(f"""\
            ## Kanal: {channel.title}
            - Handle      : {channel.handle}
            - Ülke        : {channel.country}
            - Abone       : {channel.subscriber_count:,}
            - Toplam video: {channel.video_count:,}
            - Toplam görüntülenme: {channel.total_view_count:,}
            - Kuruluş     : {channel.created_at[:10]}
            - Kanal açıklaması (ilk 400 kr): {channel.description[:400]}
            - Kanal anahtar kelimeleri: {", ".join(channel.keywords[:15]) or "—"}

            ## Son {len(recent_videos)} Videonun Özet İstatistikleri
            - Ortalama izlenme    : {avg_views:,}
            - Ortalama etkileşim  : %{avg_eng}

            ## En Çok İzlenen 5 Video
            {top5_lines}
        """)

    @staticmethod
    def video_context(
        video: VideoMetrics,
        comments: list[CommentData],
    ) -> str:
        top_comments = sorted(comments, key=lambda c: c.like_count, reverse=True)[:10]
        comment_lines = "\n".join(
            f"  [{c.like_count} beğeni] {c.author}: {c.text[:200]}"
            for c in top_comments
        ) or "  (yorum bulunamadı)"

        return dedent(f"""\
            ## Video: {video.title}
            - Video ID    : {video.video_id}
            - Kanal       : {video.channel_title}
            - Yayın tarihi: {video.published_at[:10]}
            - Süre        : {video.duration_minutes} dakika
            - İzlenme     : {video.view_count:,}
            - Beğeni      : {video.like_count:,}
            - Yorum       : {video.comment_count:,}
            - Etkileşim   : %{video.engagement_rate}
            - Etiketler   : {", ".join(video.tags[:20]) or "—"}
            - Açıklama (ilk 500 kr): {video.description[:500]}

            ## En Beğenilen 10 Yorum
            {comment_lines}
        """)

    @staticmethod
    def comments_context(
        video_title: str,
        comments: list[CommentData],
        max_comments: int = 50,
    ) -> str:
        sample = sorted(comments, key=lambda c: c.like_count, reverse=True)[:max_comments]
        lines = "\n".join(
            f"- [{c.like_count} beğeni] {c.text[:250]}"
            for c in sample
        )
        return dedent(f"""\
            ## Yorum Analizi: "{video_title}"
            Toplam yorum sayısı: {len(comments):,}
            Analiz için seçilen: {len(sample)}

            ### Yorumlar (beğeniye göre sıralı)
            {lines}
        """)

    @staticmethod
    def competitor_context(channels: list[tuple[ChannelMetrics, list[VideoMetrics]]]) -> str:
        blocks = []
        for ch, vids in channels:
            avg = round(mean(v.view_count for v in vids)) if vids else 0
            blocks.append(
                f"- **{ch.title}** — {ch.subscriber_count:,} abone | "
                f"Ort. izlenme: {avg:,}"
            )
        return "## Rakip Kanallar\n" + "\n".join(blocks)


# ------------------------------------------------------------------ #
# Ana AI Handler                                                        #
# ------------------------------------------------------------------ #

class AIHandler:
    """
    YouTube verilerini Claude'a anlamlı prompt context olarak gönderir
    ve gelen yapılandırılmış yanıtları işler.
    API anahtarı ANTHROPIC_API_KEY ortam değişkeninden okunur.
    """

    def __init__(self):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError("ANTHROPIC_API_KEY ortam değişkeni tanımlı değil.")
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
        self._max_tokens = int(os.getenv("MAX_TOKENS", "4096"))
        self._builder = PromptContextBuilder()

    # ---------------------------------------------------------------- #
    # Kanal denetimi                                                     #
    # ---------------------------------------------------------------- #

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=4, max=30))
    def audit_channel(
        self,
        channel: ChannelMetrics,
        recent_videos: list[VideoMetrics],
        competitor_data: list[tuple[ChannelMetrics, list[VideoMetrics]]] | None = None,
    ) -> ChannelAuditResult:
        """
        Kanal istatistiklerini ve video performanslarını bütünsel olarak analiz eder.
        Güçlü yönleri, gelişim alanlarını ve önerilen eylemleri yapılandırılmış döndürür.
        """
        context = self._builder.channel_context(channel, recent_videos)
        if competitor_data:
            context += "\n" + self._builder.competitor_context(competitor_data)

        prompt = dedent(f"""\
            {context}

            Yukarıdaki kanal verilerini derinlemesine incele ve şu başlıklarla raporla:

            ### GÜÇ NOKTALARI
            (Kanalın iyi yaptığı en az 4 şey, veriyle destekle)

            ### GELİŞİM ALANLARI
            (Dikkat gerektiren 3-5 alan, yapıcı bir dille ifade et)

            ### İÇERİK BOŞLUKLARI
            (Rakipler veya izleyici talebi açısından eksik kalan 3-5 konu/format)

            ### ÖNERİLEN EYLEMLER
            (Öncelik sırasıyla 5 somut, uygulanabilir adım)

            Her madde kısa (1-2 cümle) ve doğrudan uygulanabilir olsun.
        """)

        raw = self._call(prompt, context)
        return ChannelAuditResult(
            channel_title=channel.title,
            strengths=self._extract_section(raw, "GÜÇ NOKTALARI"),
            improvement_areas=self._extract_section(raw, "GELİŞİM ALANLARI"),
            content_gaps=self._extract_section(raw, "İÇERİK BOŞLUKLARI"),
            recommended_actions=self._extract_section(raw, "ÖNERİLEN EYLEMLER"),
            raw_response=raw,
        )

    # ---------------------------------------------------------------- #
    # Video performans analizi                                           #
    # ---------------------------------------------------------------- #

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=4, max=30))
    def analyze_video(
        self,
        video: VideoMetrics,
        comments: list[CommentData],
        channel_avg_views: int = 0,
    ) -> VideoAnalysisResult:
        """
        Tek bir videonun performansını ve izleyici tepkisini analiz eder.
        """
        context = self._builder.video_context(video, comments)
        benchmark = (
            f"Kanal ortalaması {channel_avg_views:,} izlenme."
            if channel_avg_views
            else ""
        )

        prompt = dedent(f"""\
            {context}
            {benchmark}

            Bu videoyu şu çerçevede değerlendir:

            ### PERFORMANS KARARI
            (1 paragraf: videonun kanal ortalamasına / sektör normlarına göre durumu)

            ### ETKİLEŞİM İÇGÖRÜLERİ
            (Beğeni oranı, yorum yoğunluğu, izlenme/abone dengesi gibi 3-5 madde)

            ### İZLEYİCİ DUYGU DURUMU
            (Yorumlara göre genel ton: olumlu / olumsuz / karışık, örneklerle açıkla)

            ### OPTİMİZASYON ÖNERİLERİ
            (Bu videonun veya benzerlerinin performansını artırmak için 4-5 somut öneri)
        """)

        raw = self._call(prompt, context)
        return VideoAnalysisResult(
            video_title=video.title,
            performance_verdict=self._extract_paragraph(raw, "PERFORMANS KARARI"),
            engagement_insights=self._extract_section(raw, "ETKİLEŞİM İÇGÖRÜLERİ"),
            audience_sentiment=self._extract_paragraph(raw, "İZLEYİCİ DUYGU DURUMU"),
            optimization_tips=self._extract_section(raw, "OPTİMİZASYON ÖNERİLERİ"),
            raw_response=raw,
        )

    # ---------------------------------------------------------------- #
    # İçerik önerileri                                                   #
    # ---------------------------------------------------------------- #

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=4, max=30))
    def recommend_content(
        self,
        channel: ChannelMetrics,
        top_videos: list[VideoMetrics],
        target_audience: str = "genel",
    ) -> ContentRecommendation:
        """
        Kanal verisi ve en iyi performanslı videolara dayanarak gelecek içerik önerir.
        """
        context = self._builder.channel_context(channel, top_videos)

        prompt = dedent(f"""\
            {context}

            Hedef kitle: {target_audience}

            Bu kanala özel içerik stratejisi öner:

            ### KONU FİKİRLERİ
            (Kanala uygun, izleyicinin ilgisini çekecek 8-10 video konusu)

            ### BAŞLIK FORMÜLLERI
            (Tıklanma oranı yüksek 5 başlık şablonu, {"{konu}"} değişkeni ile)

            ### OPTİMAL VİDEO SÜRESİ
            (Sadece dakika cinsinden bir tam sayı yaz, örnek: 12)

            ### EN İYİ YAYIM ZAMANI
            (Gün ve saat önerisi, 1 cümle)

            ### SEO ANAHTAR KELİMELERİ
            (Virgülle ayrılmış 10-15 anahtar kelime)
        """)

        raw = self._call(prompt, context)
        duration_str = self._extract_paragraph(raw, "OPTİMAL VİDEO SÜRESİ")
        duration = self._safe_int(duration_str, default=10)

        return ContentRecommendation(
            topic_ideas=self._extract_section(raw, "KONU FİKİRLERİ"),
            title_formulas=self._extract_section(raw, "BAŞLIK FORMÜLLERI"),
            optimal_duration_minutes=duration,
            best_posting_time=self._extract_paragraph(raw, "EN İYİ YAYIM ZAMANI"),
            seo_keywords=self._extract_csv(raw, "SEO ANAHTAR KELİMELERİ"),
            raw_response=raw,
        )

    # ---------------------------------------------------------------- #
    # Yorum özetleme                                                     #
    # ---------------------------------------------------------------- #

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=4, max=30))
    def summarize_comments(
        self,
        video: VideoMetrics,
        comments: list[CommentData],
    ) -> CommentSummary:
        """
        Video yorumlarını duygu analizi ve tema sınıflandırmasıyla özetler.
        """
        context = self._builder.comments_context(video.title, comments)

        prompt = dedent(f"""\
            {context}

            Bu video yorumlarını analiz et:

            ### GENEL DUYGU DURUMU
            (positive / negative / mixed — 1-2 cümleyle açıkla)

            ### ANA TEMALAR
            (Yorumlarda öne çıkan 4-6 ana konu veya tema)

            ### SIK SORULAN SORULAR
            (İzleyicilerin tekrarlayan 3-5 sorusu)

            ### ÖVGÜ NOKTALARI
            (En sık beğenilen / olumlu vurgulanan 3-5 madde)

            ### ELEŞTİRİ NOKTALARI
            (Yapıcı bir dille: geliştirilmesi gereken 3-5 alan)
        """)

        raw = self._call(prompt, context)
        sentiment_raw = self._extract_paragraph(raw, "GENEL DUYGU DURUMU").lower()
        if "positive" in sentiment_raw or "olumlu" in sentiment_raw:
            sentiment = "positive"
        elif "negative" in sentiment_raw or "olumsuz" in sentiment_raw:
            sentiment = "negative"
        else:
            sentiment = "mixed"

        return CommentSummary(
            overall_sentiment=sentiment,
            top_themes=self._extract_section(raw, "ANA TEMALAR"),
            frequently_asked_questions=self._extract_section(raw, "SIK SORULAN SORULAR"),
            praise_highlights=self._extract_section(raw, "ÖVGÜ NOKTALARI"),
            criticism_highlights=self._extract_section(raw, "ELEŞTİRİ NOKTALARI"),
            raw_response=raw,
        )

    # ---------------------------------------------------------------- #
    # Streaming — gerçek zamanlı çıktı için                             #
    # ---------------------------------------------------------------- #

    def stream_analysis(
        self,
        context: str,
        instruction: str,
    ) -> Iterator[str]:
        """Ham bağlam + talimat ile Claude'dan metin parçaları akışı döndürür."""
        with self._client.messages.stream(
            model=self._model,
            max_tokens=self._max_tokens,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"{context}\n\n{instruction}"}],
        ) as stream:
            yield from stream.text_stream

    # ---------------------------------------------------------------- #
    # Dahili yardımcılar                                                 #
    # ---------------------------------------------------------------- #

    def _call(self, prompt: str, cached_context: str = "") -> str:
        """Büyük bağlam blokları için prompt caching kullanır."""
        system_blocks: list[dict] = [{"type": "text", "text": _SYSTEM_PROMPT}]
        if len(cached_context) > 1000:
            system_blocks.append({
                "type": "text",
                "text": cached_context,
                "cache_control": {"type": "ephemeral"},
            })
            user_content = prompt.replace(cached_context, "").strip() or prompt
        else:
            user_content = prompt

        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system_blocks,
            messages=[{"role": "user", "content": user_content}],
        )
        return response.content[0].text

    @staticmethod
    def _extract_section(text: str, heading: str) -> list[str]:
        """### HEADING altındaki madde listesini döndürür."""
        items: list[str] = []
        inside = False
        for line in text.splitlines():
            if heading in line:
                inside = True
                continue
            if inside:
                stripped = line.strip()
                if stripped.startswith("###") and heading not in stripped:
                    break
                if stripped and stripped[0] in "-*•" or (
                    len(stripped) > 2 and stripped[0].isdigit() and stripped[1] in ".)"
                ):
                    clean = stripped.lstrip("-*•0123456789.) ").strip()
                    if clean:
                        items.append(clean)
        return items

    @staticmethod
    def _extract_paragraph(text: str, heading: str) -> str:
        """### HEADING altındaki ilk düz metin paragrafını döndürür."""
        lines: list[str] = []
        inside = False
        for line in text.splitlines():
            if heading in line:
                inside = True
                continue
            if inside:
                stripped = line.strip()
                if stripped.startswith("###") and heading not in stripped:
                    break
                if stripped:
                    lines.append(stripped)
                elif lines:
                    break
        return " ".join(lines).strip()

    @staticmethod
    def _extract_csv(text: str, heading: str) -> list[str]:
        """### HEADING altındaki virgülle ayrılmış değerleri liste olarak döndürür."""
        para = AIHandler._extract_paragraph(text, heading)
        return [kw.strip() for kw in para.split(",") if kw.strip()]

    @staticmethod
    def _safe_int(text: str, default: int = 10) -> int:
        for token in text.split():
            cleaned = "".join(c for c in token if c.isdigit())
            if cleaned:
                return int(cleaned)
        return default
