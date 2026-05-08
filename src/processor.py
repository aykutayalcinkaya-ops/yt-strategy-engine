"""
src/processor.py
~~~~~~~~~~~~~~~~
Algoritmik veri işleme katmanı.

YouTube'dan gelen ham VideoMetrics ve isteğe bağlı Analytics verilerini
alır; istatistiksel yöntemlerle Shorts / Long-form karşılaştırması,
başarı skorlaması, kazanan örüntü çıkarımı ve teknik sorun teşhisi yapar.
Claude çağrısı yapmaz — çıktılar ai_handler.py'ye hazır prompt bağlamı
olarak teslim edilir.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from statistics import mean, median, pstdev
from textwrap import dedent
from typing import Optional

from .youtube_client import VideoMetrics


# ──────────────────────────────────────────────────────────────────── #
# Sabitler                                                              #
# ──────────────────────────────────────────────────────────────────── #

SHORTS_MAX_SECONDS = 60          # YouTube Shorts eşiği
LONG_FORM_MIN_SECONDS = 61

# Başarı skoru ağırlıkları (toplamı 1.0)
_W_VIEWS = 0.45
_W_ENGAGEMENT = 0.35
_W_RECENCY = 0.20

# Yüzdelik eşikler
_TOP_PERCENTILE = 0.80       # üst %20 → kazanan
_BOTTOM_PERCENTILE = 0.30    # alt %30 → düzeltme adayı

# Teknik sağlık kriterleri
_MIN_TAGS = 5
_MIN_TITLE_LEN = 35
_MAX_TITLE_LEN = 70
_MIN_DESCRIPTION_LEN = 200
_LOW_ENGAGEMENT_THRESHOLD = 0.5   # %0.5 altı düşük etkileşim

# Türkçe + yaygın İngilizce stop-kelimeler (başlık analizi için)
_STOPWORDS = {
    "ve", "ile", "bir", "bu", "da", "de", "için", "mi", "mı", "mu", "mü",
    "ne", "en", "çok", "daha", "olan", "gibi", "var", "yok", "ben", "sen",
    "the", "a", "an", "is", "in", "on", "at", "to", "of", "and", "or",
    "how", "why", "what", "when", "2024", "2025",
}


# ──────────────────────────────────────────────────────────────────── #
# Giriş veri yapıları                                                   #
# ──────────────────────────────────────────────────────────────────── #

@dataclass
class VideoAnalyticsInput:
    """
    YouTube Analytics API'sinden gelen, OAuth gerektiren ek metrikler.
    Sağlanmazsa proxy tahminleri kullanılır.
    """
    video_id: str
    watch_time_seconds: float = 0.0          # toplam izlenme süresi (saniye)
    avg_view_duration_seconds: float = 0.0   # ortalama izlenme süresi
    impressions: int = 0                     # gösterim sayısı
    impression_ctr: float = 0.0             # gerçek CTR (0.0–1.0)
    subscriber_gained: int = 0
    subscriber_lost: int = 0


# ──────────────────────────────────────────────────────────────────── #
# Hesaplanmış video skoru                                               #
# ──────────────────────────────────────────────────────────────────── #

class VideoFormat(str, Enum):
    SHORT = "short"
    LONG_FORM = "long_form"


class PerformanceTier(str, Enum):
    TOP = "top"
    AVERAGE = "average"
    UNDERPERFORMING = "underperforming"


@dataclass
class VideoScore:
    video: VideoMetrics
    fmt: VideoFormat
    success_score: float              # 0.0 – 1.0
    view_score: float
    engagement_score: float
    recency_score: float
    estimated_ctr: float              # proxy veya Analytics'ten
    estimated_retention_pct: float   # proxy veya Analytics'ten
    viral_coefficient: float          # views / subscriber_count
    tier: PerformanceTier = PerformanceTier.AVERAGE
    analytics: Optional[VideoAnalyticsInput] = None

    # Teknik sorunlar (teşhis adımında doldurulur)
    technical_issues: list[str] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────── #
# Shorts / Long-form karşılaştırması                                    #
# ──────────────────────────────────────────────────────────────────── #

@dataclass
class FormatComparison:
    shorts_count: int
    longform_count: int

    shorts_avg_views: float
    longform_avg_views: float

    shorts_avg_engagement: float
    longform_avg_engagement: float

    shorts_avg_ctr: float
    longform_avg_ctr: float

    shorts_avg_duration_sec: float
    longform_avg_duration_sec: float

    # Abone kazanımı (Analytics varsa dolu, yoksa 0)
    shorts_subscriber_gained: int
    longform_subscriber_gained: int

    recommended_format: VideoFormat
    recommendation_reason: str


# ──────────────────────────────────────────────────────────────────── #
# Kazanan örüntüler                                                     #
# ──────────────────────────────────────────────────────────────────── #

@dataclass
class WinningPattern:
    """Üst %20 videodan damıtılmış başarı sinyalleri."""
    top_title_keywords: list[str]           # en sık geçen başlık kelimeleri
    top_tags: list[str]                     # en yaygın etiketler
    optimal_duration_range: tuple[int, int] # (min_sn, max_sn)
    optimal_format: VideoFormat
    avg_title_length: float
    uses_numbers_in_title: float            # oran: 0.0–1.0
    uses_question_in_title: float
    uses_emotional_trigger: float           # "nasıl", "neden", "en iyi" vb.
    best_publish_days: list[str]            # ["Pazartesi", "Salı"]
    median_tag_count: float
    median_description_length: float


# ──────────────────────────────────────────────────────────────────── #
# Teknik düzeltme adayı                                                 #
# ──────────────────────────────────────────────────────────────────── #

@dataclass
class TechnicalFixItem:
    video: VideoMetrics
    priority: int                  # 1 = acil, 2 = yüksek, 3 = orta
    issues: list[str]              # teşhis edilen sorunlar
    fix_suggestions: list[str]     # her sorun için somut öneri


# ──────────────────────────────────────────────────────────────────── #
# Ana işlemci raporu                                                     #
# ──────────────────────────────────────────────────────────────────── #

@dataclass
class ProcessorReport:
    channel_id: str
    channel_title: str
    total_videos_analyzed: int
    generated_at: str

    format_comparison: FormatComparison
    winning_patterns: WinningPattern
    top_performers: list[VideoScore]        # üst %20
    underperformers: list[VideoScore]       # alt %30

    # Çıktı sorusu 1: Hangi konularda video çekilmeli?
    topic_recommendations: list[str]

    # Çıktı sorusu 2: Hangi videolarda teknik düzeltme yapılmalı?
    technical_fix_candidates: list[TechnicalFixItem]

    # ai_handler.py'ye doğrudan geçilebilecek zengin prompt bağlamı
    strategy_prompt_context: str


# ──────────────────────────────────────────────────────────────────── #
# Ana işlemci sınıfı                                                    #
# ──────────────────────────────────────────────────────────────────── #

class VideoProcessor:
    """
    Ham VideoMetrics listesini alıp istatistiksel analiz ile
    ProcessorReport üretir.

    Kullanım:
        processor = VideoProcessor(subscriber_count=125_000)
        report = processor.run(videos, analytics_inputs=[...])
        # report.strategy_prompt_context → AIHandler'a gönder
    """

    def __init__(self, subscriber_count: int = 0, channel_id: str = "", channel_title: str = ""):
        self._subscriber_count = subscriber_count
        self._channel_id = channel_id
        self._channel_title = channel_title

    # ────────────────────────────────────────────────────────────────── #
    # Genel giriş noktası                                                 #
    # ────────────────────────────────────────────────────────────────── #

    def run(
        self,
        videos: list[VideoMetrics],
        analytics_inputs: list[VideoAnalyticsInput] | None = None,
    ) -> ProcessorReport:
        if not videos:
            raise ValueError("En az bir VideoMetrics nesnesi gereklidir.")

        analytics_map = {a.video_id: a for a in (analytics_inputs or [])}
        scored = self._score_all(videos, analytics_map)
        self._assign_tiers(scored)
        self._diagnose_technical_issues(scored)

        tops = [s for s in scored if s.tier == PerformanceTier.TOP]
        bottoms = [s for s in scored if s.tier == PerformanceTier.UNDERPERFORMING]

        fmt_cmp = self._compare_formats(scored, analytics_map)
        patterns = self._extract_winning_patterns(tops)
        topics = self._recommend_topics(tops, patterns)
        fixes = self._build_fix_list(bottoms)
        context = self._build_prompt_context(scored, fmt_cmp, patterns, topics, fixes)

        return ProcessorReport(
            channel_id=self._channel_id,
            channel_title=self._channel_title,
            total_videos_analyzed=len(videos),
            generated_at=datetime.now(timezone.utc).isoformat(),
            format_comparison=fmt_cmp,
            winning_patterns=patterns,
            top_performers=sorted(tops, key=lambda s: s.success_score, reverse=True),
            underperformers=sorted(bottoms, key=lambda s: s.success_score),
            topic_recommendations=topics,
            technical_fix_candidates=fixes,
            strategy_prompt_context=context,
        )

    # ────────────────────────────────────────────────────────────────── #
    # 1. Skorlama                                                         #
    # ────────────────────────────────────────────────────────────────── #

    def _score_all(
        self,
        videos: list[VideoMetrics],
        analytics_map: dict[str, VideoAnalyticsInput],
    ) -> list[VideoScore]:
        max_views = max(v.view_count for v in videos) or 1
        max_eng = max(v.engagement_rate for v in videos) or 1
        now = datetime.now(timezone.utc)

        ages_days = [self._age_days(v, now) for v in videos]
        max_age = max(ages_days) or 1

        scored: list[VideoScore] = []
        for video, age in zip(videos, ages_days):
            analytics = analytics_map.get(video.video_id)
            fmt = (
                VideoFormat.SHORT
                if video.duration_seconds <= SHORTS_MAX_SECONDS
                else VideoFormat.LONG_FORM
            )

            view_score = video.view_count / max_views
            eng_score = video.engagement_rate / max_eng
            # Daha yeni video → daha yüksek skor; log baskısıyla normalize
            recency_score = 1.0 - math.log1p(age) / math.log1p(max_age)

            success = (
                _W_VIEWS * view_score
                + _W_ENGAGEMENT * eng_score
                + _W_RECENCY * recency_score
            )

            ctr = self._estimate_ctr(video, analytics)
            retention = self._estimate_retention(video, analytics)
            viral = (
                video.view_count / self._subscriber_count
                if self._subscriber_count > 0
                else 0.0
            )

            scored.append(VideoScore(
                video=video,
                fmt=fmt,
                success_score=round(success, 4),
                view_score=round(view_score, 4),
                engagement_score=round(eng_score, 4),
                recency_score=round(recency_score, 4),
                estimated_ctr=round(ctr, 4),
                estimated_retention_pct=round(retention, 2),
                viral_coefficient=round(viral, 4),
                analytics=analytics,
            ))
        return scored

    # ────────────────────────────────────────────────────────────────── #
    # 2. Tier ataması (yüzdelik dilim)                                    #
    # ────────────────────────────────────────────────────────────────── #

    @staticmethod
    def _assign_tiers(scored: list[VideoScore]) -> None:
        scores = sorted(s.success_score for s in scored)
        top_thresh = scores[max(0, int(len(scores) * _TOP_PERCENTILE))]
        bot_thresh = scores[min(len(scores) - 1, int(len(scores) * _BOTTOM_PERCENTILE))]

        for s in scored:
            if s.success_score >= top_thresh:
                s.tier = PerformanceTier.TOP
            elif s.success_score <= bot_thresh:
                s.tier = PerformanceTier.UNDERPERFORMING
            else:
                s.tier = PerformanceTier.AVERAGE

    # ────────────────────────────────────────────────────────────────── #
    # 3. Teknik sorun teşhisi                                             #
    # ────────────────────────────────────────────────────────────────── #

    def _diagnose_technical_issues(self, scored: list[VideoScore]) -> None:
        corpus_eng_rates = [s.video.engagement_rate for s in scored]
        p25_eng = self._percentile(corpus_eng_rates, 25)

        for s in scored:
            v = s.video
            issues: list[str] = []

            if v.engagement_rate < _LOW_ENGAGEMENT_THRESHOLD:
                issues.append(
                    f"Çok düşük etkileşim oranı (%{v.engagement_rate:.2f}) — "
                    "thumbnail veya başlık izleyici beklentisini karşılamıyor olabilir."
                )
            elif v.engagement_rate < p25_eng:
                issues.append(
                    f"Düşük etkileşim (corpus alt %25 — %{v.engagement_rate:.2f}): "
                    "CTA eksikliği veya zayıf ilk 30 saniye hook'u."
                )

            if len(v.tags) < _MIN_TAGS:
                issues.append(
                    f"Yetersiz etiket ({len(v.tags)} adet < min {_MIN_TAGS}): "
                    "SEO görünürlüğü düşük."
                )

            title_len = len(v.title)
            if title_len < _MIN_TITLE_LEN:
                issues.append(
                    f"Başlık çok kısa ({title_len} kr < {_MIN_TITLE_LEN}): "
                    "arama algoritmalarında yeterli sinyal vermiyor."
                )
            elif title_len > _MAX_TITLE_LEN:
                issues.append(
                    f"Başlık çok uzun ({title_len} kr > {_MAX_TITLE_LEN}): "
                    "mobilde kesilebilir, tıklama oranı düşebilir."
                )

            if len(v.description) < _MIN_DESCRIPTION_LEN:
                issues.append(
                    f"Açıklama yetersiz ({len(v.description)} kr): "
                    "anahtar kelime yoğunluğu düşük, SEO fırsatı kaçırılıyor."
                )

            if s.estimated_ctr < 0.02 and s.analytics and s.analytics.impressions > 0:
                issues.append(
                    f"Düşük gerçek CTR (%{s.estimated_ctr*100:.1f}): "
                    "thumbnail A/B testi ve başlık reformülasyonu gerekli."
                )
            elif s.estimated_ctr < 0.03 and not s.analytics:
                issues.append(
                    "Tahmini CTR proxy düşük: thumbnail veya başlık güçlendirilmeli."
                )

            if s.estimated_retention_pct < 30.0:
                issues.append(
                    f"Tahmini izlenme tutma oranı düşük (%{s.estimated_retention_pct:.0f}): "
                    "içerik yapısı, intro hook veya tempo sorunlu olabilir."
                )

            s.technical_issues = issues

    # ────────────────────────────────────────────────────────────────── #
    # 4. Shorts / Long-form karşılaştırması                               #
    # ────────────────────────────────────────────────────────────────── #

    def _compare_formats(
        self,
        scored: list[VideoScore],
        analytics_map: dict[str, VideoAnalyticsInput],
    ) -> FormatComparison:
        shorts = [s for s in scored if s.fmt == VideoFormat.SHORT]
        longs = [s for s in scored if s.fmt == VideoFormat.LONG_FORM]

        def _avg(items: list[VideoScore], attr: str) -> float:
            vals = [getattr(s, attr) for s in items]
            return round(mean(vals), 4) if vals else 0.0

        def _avg_video(items: list[VideoScore], attr: str) -> float:
            vals = [getattr(s.video, attr) for s in items]
            return round(mean(vals), 2) if vals else 0.0

        def _subs_gained(items: list[VideoScore]) -> int:
            total = 0
            for s in items:
                a = analytics_map.get(s.video.video_id)
                if a:
                    total += a.subscriber_gained
            return total

        s_views = _avg_video(shorts, "view_count")
        l_views = _avg_video(longs, "view_count")
        s_eng = _avg_video(shorts, "engagement_rate")
        l_eng = _avg_video(longs, "engagement_rate")
        s_ctr = _avg(shorts, "estimated_ctr")
        l_ctr = _avg(longs, "estimated_ctr")
        s_dur = _avg_video(shorts, "duration_seconds")
        l_dur = _avg_video(longs, "duration_seconds")
        s_subs = _subs_gained(shorts)
        l_subs = _subs_gained(longs)

        # Format tavsiyesi: skor karşılaştırması (eğer veri yeterince varsa)
        def _avg_score(items: list[VideoScore]) -> float:
            return mean(s.success_score for s in items) if items else 0.0

        s_score = _avg_score(shorts)
        l_score = _avg_score(longs)

        if len(shorts) < 3 and len(longs) >= 3:
            rec_fmt = VideoFormat.LONG_FORM
            rec_reason = f"Shorts örneği yetersiz ({len(shorts)} video); Long-form baskın ve daha iyi skora sahip."
        elif len(longs) < 3 and len(shorts) >= 3:
            rec_fmt = VideoFormat.SHORT
            rec_reason = f"Long-form örneği yetersiz ({len(longs)} video); Shorts baskın."
        elif s_score > l_score * 1.1:
            rec_fmt = VideoFormat.SHORT
            rec_reason = (
                f"Shorts ortalama başarı skoru ({s_score:.3f}) Long-form'dan "
                f"(%{((s_score/max(l_score,0.001))-1)*100:.0f}) daha yüksek."
            )
        elif l_score > s_score * 1.1:
            rec_fmt = VideoFormat.LONG_FORM
            rec_reason = (
                f"Long-form ortalama başarı skoru ({l_score:.3f}) Shorts'tan "
                f"(%{((l_score/max(s_score,0.001))-1)*100:.0f}) daha yüksek."
            )
        else:
            rec_fmt = VideoFormat.LONG_FORM
            rec_reason = "Performans yakın; Long-form abone bağlılığı ve SEO için tercih edilir."

        return FormatComparison(
            shorts_count=len(shorts),
            longform_count=len(longs),
            shorts_avg_views=s_views,
            longform_avg_views=l_views,
            shorts_avg_engagement=s_eng,
            longform_avg_engagement=l_eng,
            shorts_avg_ctr=s_ctr,
            longform_avg_ctr=l_ctr,
            shorts_avg_duration_sec=s_dur,
            longform_avg_duration_sec=l_dur,
            shorts_subscriber_gained=s_subs,
            longform_subscriber_gained=l_subs,
            recommended_format=rec_fmt,
            recommendation_reason=rec_reason,
        )

    # ────────────────────────────────────────────────────────────────── #
    # 5. Kazanan örüntü çıkarımı                                          #
    # ────────────────────────────────────────────────────────────────── #

    @staticmethod
    def _extract_winning_patterns(tops: list[VideoScore]) -> WinningPattern:
        if not tops:
            return WinningPattern(
                top_title_keywords=[], top_tags=[], optimal_duration_range=(0, 0),
                optimal_format=VideoFormat.LONG_FORM, avg_title_length=0.0,
                uses_numbers_in_title=0.0, uses_question_in_title=0.0,
                uses_emotional_trigger=0.0, best_publish_days=[], median_tag_count=0.0,
                median_description_length=0.0,
            )

        # Başlık kelime analizi
        word_counter: Counter = Counter()
        for s in tops:
            tokens = re.findall(r"\b\w{3,}\b", s.video.title.lower())
            word_counter.update(t for t in tokens if t not in _STOPWORDS)
        top_keywords = [w for w, _ in word_counter.most_common(15)]

        # Etiket analizi
        tag_counter: Counter = Counter()
        for s in tops:
            tag_counter.update(t.lower() for t in s.video.tags)
        top_tags = [t for t, _ in tag_counter.most_common(20)]

        # Optimal süre aralığı (IQR)
        durations = sorted(s.video.duration_seconds for s in tops)
        q1 = durations[len(durations) // 4]
        q3 = durations[3 * len(durations) // 4]
        opt_range = (q1, q3)

        # Baskın format
        fmt_count = Counter(s.fmt for s in tops)
        opt_fmt = fmt_count.most_common(1)[0][0]

        # Başlık istatistikleri
        n = len(tops)
        avg_title_len = mean(len(s.video.title) for s in tops)
        uses_numbers = sum(1 for s in tops if re.search(r"\d", s.video.title)) / n
        uses_question = sum(1 for s in tops if "?" in s.video.title) / n
        emotional_kw = {"nasıl", "neden", "en", "iyi", "kötü", "sır", "hata",
                        "öğren", "kazan", "başar", "hızlı", "kolay", "doğru", "yanlış",
                        "how", "why", "best", "worst", "secret", "fast", "easy"}
        uses_emotional = sum(
            1 for s in tops
            if any(w in s.video.title.lower() for w in emotional_kw)
        ) / n

        # Yayın günü analizi
        day_counter: Counter = Counter()
        tr_days = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
        for s in tops:
            try:
                dt = datetime.fromisoformat(s.video.published_at.replace("Z", "+00:00"))
                day_counter[tr_days[dt.weekday()]] += 1
            except (ValueError, AttributeError):
                pass
        best_days = [d for d, _ in day_counter.most_common(3)]

        return WinningPattern(
            top_title_keywords=top_keywords,
            top_tags=top_tags,
            optimal_duration_range=opt_range,
            optimal_format=opt_fmt,
            avg_title_length=round(avg_title_len, 1),
            uses_numbers_in_title=round(uses_numbers, 2),
            uses_question_in_title=round(uses_question, 2),
            uses_emotional_trigger=round(uses_emotional, 2),
            best_publish_days=best_days,
            median_tag_count=round(median(len(s.video.tags) for s in tops), 1),
            median_description_length=round(median(len(s.video.description) for s in tops), 0),
        )

    # ────────────────────────────────────────────────────────────────── #
    # 6. Konu önerileri (Çıktı Sorusu 1)                                  #
    # ────────────────────────────────────────────────────────────────── #

    @staticmethod
    def _recommend_topics(
        tops: list[VideoScore],
        patterns: WinningPattern,
    ) -> list[str]:
        """
        Üst performanslı videoların başlıklarından ve örüntülerden
        yeni video konusu önerisi üretir.
        Tamamen deterministik; Claude çağrısı yapmaz.
        """
        if not tops:
            return []

        # Başarılı başlıkları konu çekirdeği olarak kullan
        keyword_pairs: list[str] = []
        for s in tops[:10]:
            words = [
                w for w in re.findall(r"\b\w{4,}\b", s.video.title.lower())
                if w not in _STOPWORDS
            ]
            if len(words) >= 2:
                keyword_pairs.append(f"{words[0]} {words[1]}")

        # En tekrarlayan çift → yeni başlık önerisi
        pair_counter = Counter(keyword_pairs)
        topic_seeds = [p for p, _ in pair_counter.most_common(5)]

        # Kazanan örüntüleri konulara ekle
        recommendations: list[str] = []
        kw = patterns.top_title_keywords

        # Sayı kullanan başlık formülü
        if kw:
            recommendations.append(
                f"'{kw[0]}' konusunda '5 Adımda ...' veya 'En İyi 7 ...' formatında liste videosu"
            )
        # Soru formatı (yüksek CTR sinyali)
        if len(kw) >= 2:
            recommendations.append(
                f"'{kw[1]}' hakkında soru başlıklı deep-dive (örn: '... neden çalışmıyor?')"
            )
        # Başarılı etiket kümesine dayalı öneri
        if patterns.top_tags:
            tag_group = ", ".join(patterns.top_tags[:3])
            recommendations.append(
                f"'{tag_group}' etiket kümesini hedefleyen başlangıç rehberi"
            )
        # Uzun-form sweet spot'a uyan içerik
        lo, hi = patterns.optimal_duration_range
        recommendations.append(
            f"Optimal süre aralığı ({lo//60}–{hi//60} dk) için {patterns.optimal_format.value} format: "
            f"'{kw[0] if kw else 'popüler konu'}' vaka çalışması veya karşılaştırma videosu"
        )
        # Duygusal tetikleyici kullanan öneri
        if patterns.uses_emotional_trigger > 0.5 and len(kw) >= 3:
            recommendations.append(
                f"'{kw[2]}' konusunda 'Herkesin Yaptığı {kw[2].title()} Hataları' tarzı hata-analizi videosu"
            )
        # Tohum çiftlerinden ek öneriler
        for seed in topic_seeds[:3]:
            reco = f"'{seed}' çekirdeğinden genişletilmiş içerik — karşılaştırma veya güncel haber odaklı"
            if reco not in recommendations:
                recommendations.append(reco)

        return recommendations[:10]

    # ────────────────────────────────────────────────────────────────── #
    # 7. Teknik düzeltme listesi (Çıktı Sorusu 2)                         #
    # ────────────────────────────────────────────────────────────────── #

    @staticmethod
    def _build_fix_list(bottoms: list[VideoScore]) -> list[TechnicalFixItem]:
        """
        Düşük performanslı videoları öncelik sırasıyla düzeltme listesine alır.
        Her sorun için somut, uygulanabilir öneri üretir.
        """
        _FIX_MAP: dict[str, str] = {
            "Çok düşük etkileşim": (
                "Thumbnail'ı A/B test et (en az 2 varyant); başlıkta merak boşluğu yarat. "
                "İlk 30 saniyeye güçlü hook ekle: 'Bu videoda X'i öğreneceksin.' diyerek aç."
            ),
            "Düşük etkileşim": (
                "Video ortasına ve sonuna açık CTA ekle ('Düşüncelerini yorumlara yaz!'). "
                "End-screen kartlarını optimize et."
            ),
            "Yetersiz etiket": (
                "YouTube Studio'da 15-20 etiket ekle: 3-5 ana anahtar kelime + uzun kuyruklu varyantlar. "
                "TubeBuddy veya vidIQ ile rakip etiketleri analiz et."
            ),
            "Başlık çok kısa": (
                "Başlığı 50-60 karaktere çıkar; anahtar kelimeyi başa al, sayı veya güçlü sıfat ekle."
            ),
            "Başlık çok uzun": (
                "Başlığı 60-65 karaktere kısalt; ana anahtar kelimeyi ilk 60 karakterde tut."
            ),
            "Açıklama yetersiz": (
                "İlk 200 karaktere anahtar kelimeyi yerleştir, timestamps/bölüm linkleri ekle, "
                "diğer ilgili videolara bağlantı ver."
            ),
            "Düşük gerçek CTR": (
                "Thumbnail renk kontrastını artır, yüz ifadesi veya metin overlay ekle. "
                "Başlıkta 'Bu videoyu izleme sebebin' kanca ifadesi dene."
            ),
            "Tahmini CTR proxy düşük": (
                "Thumbnail'ı güncelle ve başlığı reformüle et; rakiplerin thumbnail formatını incele."
            ),
            "Tahmini izlenme tutma": (
                "İlk 30 saniyeyi yeniden kur: doğrudan konuya gir, uzun intro kaldır. "
                "3-5 dakikada bir 'merak boşluğu döngüsü' kır."
            ),
        }

        fix_items: list[TechnicalFixItem] = []
        for s in bottoms:
            if not s.technical_issues:
                continue
            suggestions: list[str] = []
            for issue in s.technical_issues:
                for key, fix in _FIX_MAP.items():
                    if key in issue:
                        suggestions.append(fix)
                        break
                else:
                    suggestions.append("Genel performans incelemesi için YouTube Analytics'i kontrol et.")

            # Öncelik: sorun sayısı + tier kombinasyonu
            priority = 1 if len(s.technical_issues) >= 3 else (2 if len(s.technical_issues) == 2 else 3)

            fix_items.append(TechnicalFixItem(
                video=s.video,
                priority=priority,
                issues=s.technical_issues,
                fix_suggestions=suggestions,
            ))

        return sorted(fix_items, key=lambda f: f.priority)

    # ────────────────────────────────────────────────────────────────── #
    # 8. Strateji prompt bağlamı inşası (ai_handler'a gidecek)            #
    # ────────────────────────────────────────────────────────────────── #

    @staticmethod
    def _build_prompt_context(
        scored: list[VideoScore],
        fmt: FormatComparison,
        patterns: WinningPattern,
        topics: list[str],
        fixes: list[TechnicalFixItem],
    ) -> str:
        top5 = sorted(scored, key=lambda s: s.success_score, reverse=True)[:5]
        bot5 = sorted(scored, key=lambda s: s.success_score)[:5]
        top5_lines = "\n".join(
            f"  {i+1}. [{s.tier.value.upper()}] {s.video.title}\n"
            f"     İzlenme: {s.video.view_count:,} | Etkileşim: %{s.video.engagement_rate:.2f} | "
            f"CTR tahmini: %{s.estimated_ctr*100:.1f} | "
            f"Format: {s.fmt.value} | Skor: {s.success_score:.3f}"
            for i, s in enumerate(top5)
        )
        bot5_lines = "\n".join(
            f"  {i+1}. {s.video.title}\n"
            f"     Sorunlar: {'; '.join(s.technical_issues[:2]) or 'yok'}"
            for i, s in enumerate(bot5)
        )
        topic_lines = "\n".join(f"  {i+1}. {t}" for i, t in enumerate(topics))
        fix_lines = "\n".join(
            f"  P{f.priority}: '{f.video.title[:60]}' — {len(f.issues)} sorun"
            for f in fixes[:8]
        )
        opt_lo, opt_hi = patterns.optimal_duration_range

        return dedent(f"""\
            ## İşlemci Analiz Raporu

            ### Shorts / Long-form Karşılaştırması
            | Metrik              | Shorts ({fmt.shorts_count})      | Long-form ({fmt.longform_count})  |
            |---------------------|--------------------------|---------------------------|
            | Ort. izlenme        | {fmt.shorts_avg_views:,.0f}           | {fmt.longform_avg_views:,.0f}            |
            | Ort. etkileşim      | %{fmt.shorts_avg_engagement:.2f}            | %{fmt.longform_avg_engagement:.2f}             |
            | Tahmini ort. CTR    | %{fmt.shorts_avg_ctr*100:.1f}              | %{fmt.longform_avg_ctr*100:.1f}               |
            | Abone kazanımı      | {fmt.shorts_subscriber_gained:,}              | {fmt.longform_subscriber_gained:,}               |
            **Öneri:** {fmt.recommended_format.value.upper()} — {fmt.recommendation_reason}

            ### Kazanan Örüntüler (Üst %20)
            - En etkili başlık kelimeleri: {", ".join(patterns.top_title_keywords[:10])}
            - En etkili etiketler: {", ".join(patterns.top_tags[:10])}
            - Optimal süre aralığı: {opt_lo//60}–{opt_hi//60} dakika
            - Başlıkta sayı kullanım oranı: %{patterns.uses_numbers_in_title*100:.0f}
            - Başlıkta soru kullanım oranı: %{patterns.uses_question_in_title*100:.0f}
            - Duygusal tetikleyici kullanımı: %{patterns.uses_emotional_trigger*100:.0f}
            - En iyi yayın günleri: {", ".join(patterns.best_publish_days) or "—"}
            - Medyan etiket sayısı: {patterns.median_tag_count}
            - Medyan açıklama uzunluğu: {patterns.median_description_length:.0f} karakter

            ### En İyi 5 Video
            {top5_lines}

            ### En Düşük 5 Video
            {bot5_lines}

            ### ÇıKTI 1 — Hangi Konularda Video Çekilmeli?
            {topic_lines}

            ### ÇIKTI 2 — Hangi Videolarda Teknik Düzeltme Yapılmalı? (Öncelik sırası)
            {fix_lines if fix_lines else "  Ciddi teknik sorun tespit edilmedi."}
        """).strip()

    # ────────────────────────────────────────────────────────────────── #
    # Yardımcı hesaplamalar                                               #
    # ────────────────────────────────────────────────────────────────── #

    @staticmethod
    def _estimate_ctr(video: VideoMetrics, analytics: Optional[VideoAnalyticsInput]) -> float:
        """
        Gerçek Analytics verisi varsa onu kullanır.
        Yoksa (beğeni + yorum) / görüntülenme'yi CTR proxy'si olarak hesaplar
        ve 0.02–0.12 arasında sıkıştırır.
        """
        if analytics and analytics.impression_ctr > 0:
            return analytics.impression_ctr
        if video.view_count == 0:
            return 0.0
        raw = (video.like_count + video.comment_count) / video.view_count
        # Tipik YouTube CTR aralığına normalize et
        return min(max(raw * 0.4, 0.01), 0.15)

    @staticmethod
    def _estimate_retention(video: VideoMetrics, analytics: Optional[VideoAnalyticsInput]) -> float:
        """
        Gerçek Analytics verisi varsa onu kullanır.
        Yoksa: etkileşim skoru ve süreyi proxy olarak kullanır.
        Kısa videolar genellikle daha yüksek retention'a sahip olduğundan
        süreye göre bir düzeltme katsayısı eklenir.
        """
        if analytics and analytics.avg_view_duration_seconds > 0 and video.duration_seconds > 0:
            return min(analytics.avg_view_duration_seconds / video.duration_seconds * 100, 100.0)

        if video.view_count == 0 or video.duration_seconds == 0:
            return 0.0

        eng_factor = min(video.engagement_rate / 5.0, 1.0)   # %5 etkileşim → tam puan
        dur_factor = 1.0 - min(video.duration_seconds / 3600, 1.0) * 0.5  # uzun video cezası
        return round(30.0 + eng_factor * 40.0 * dur_factor, 1)  # 30–70 arası

    @staticmethod
    def _age_days(video: VideoMetrics, now: datetime) -> float:
        try:
            published = datetime.fromisoformat(video.published_at.replace("Z", "+00:00"))
            return max((now - published).days, 0)
        except (ValueError, AttributeError):
            return 365.0

    @staticmethod
    def _percentile(data: list[float], pct: int) -> float:
        if not data:
            return 0.0
        sorted_data = sorted(data)
        idx = int(len(sorted_data) * pct / 100)
        return sorted_data[min(idx, len(sorted_data) - 1)]
