"""
src/ai_strategist.py
~~~~~~~~~~~~~~~~~~~~
file_processor.py'dan gelen sayısal özetleri alır, yapılandırılmış
bir prompt şablonuyla Claude'a gönderir ve strateji raporu üretir.

Temel şablon:
  "Kanalımın son [DÖNEM] verileri şunlar: [VERİ].
   Bu verilere dayanarak, kitleyi elimde tutmak için
   hangi içerik türüne odaklanmalıyım?"

Ek sorgu türleri: kitle tutma, büyüme stratejisi, konu fikirleri,
teknik iyileştirme — her biri aynı veri bloğunu farklı odakla kullanır.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from textwrap import dedent
from typing import Iterator

import anthropic
from tenacity import retry, stop_after_attempt, wait_exponential

from .file_processor import AggregatedStats, FileReport, MergedReport, VideoSummaryRow


# ──────────────────────────────────────────────────────────────────── #
# Sistem promptu — üç temel prensip                                     #
# ──────────────────────────────────────────────────────────────────── #

_SYSTEM_PROMPT = dedent("""\
    Sen, YouTube kanal büyümesi ve içerik stratejisi konusunda uzmanlaşmış
    bir veri odaklı danışmansın.

    Yanıtlarında üç prensip kesinlikle uygulanır:

    ─── Teknik Doğruluk ───────────────────────────────────────────────
    Her öneri sağlanan gerçek metrik verisine dayanır.
    Spekülasyon yapmak yerine "veri yetersiz" ya da "bu dönemde gözlemlenen"
    gibi ifadeler kullan. Sayıları yanlış yansıtma.

    ─── Objektif Analiz ───────────────────────────────────────────────
    Hem güçlü yönleri hem gelişim alanlarını dengeli biçimde sun.
    Tek taraflı methiye veya eleştiriden kaçın.
    Birden fazla seçeneği karşılaştırarak kanıta dayalı öneri yap.

    ─── Yapıcı Ton (Praising) ─────────────────────────────────────────
    İçerik üreticisini motive et; başarıları somut metriklerle kutla.
    Gelişim alanlarını "fırsat" çerçevesiyle sun, asla yargılama.
    Öneri listelerini cesaretlendirici bir kapanışla bitir.
""")


# ──────────────────────────────────────────────────────────────────── #
# Sorgu türleri                                                          #
# ──────────────────────────────────────────────────────────────────── #

class QueryType(str, Enum):
    CONTENT_FOCUS      = "content_focus"       # ana şablon: hangi içerik türü
    AUDIENCE_RETENTION = "audience_retention"  # kitleyi elde tutma
    GROWTH_STRATEGY    = "growth_strategy"     # abone ve izlenme büyümesi
    TOPIC_IDEAS        = "topic_ideas"         # somut video konusu önerileri
    TECHNICAL_FIXES    = "technical_fixes"     # CTR / izleme süresi iyileştirme
    CUSTOM             = "custom"              # serbest soru


# Her sorgu türüne karşılık gelen odak sorusu
_QUERY_FOCUS: dict[QueryType, str] = {
    QueryType.CONTENT_FOCUS: (
        "Bu verilere dayanarak, kitleyi elimde tutmak için "
        "hangi içerik türüne odaklanmalıyım?"
    ),
    QueryType.AUDIENCE_RETENTION: (
        "Ortalama izleme sürem ve CTR verilerime bakarak, "
        "izleyicilerin videoyu terk etmesini önlemek için "
        "ne tür içerik yapısı ve format önerirsin?"
    ),
    QueryType.GROWTH_STRATEGY: (
        "Abone kazanımım ve izlenme trendlerime göre, "
        "kanalımı büyütmek için hangi büyüme stratejisini izlemeliyim?"
    ),
    QueryType.TOPIC_IDEAS: (
        "En iyi ve en kötü performanslı videolarıma bakarak, "
        "önümüzdeki 30 gün için 5 somut video konusu öner. "
        "Her öneri için neden işe yarayacağını açıkla."
    ),
    QueryType.TECHNICAL_FIXES: (
        "CTR ve ortalama izleme süresindeki zayıf noktalara göre, "
        "thumbnail, başlık ve video yapısında hangi teknik "
        "iyileştirmeleri yapmalıyım? Önceliklere göre sırala."
    ),
}

# Yanıt bölüm başlıkları — Claude'dan bu yapıyla çıktı istiyoruz
_RESPONSE_SECTIONS = {
    QueryType.CONTENT_FOCUS: [
        "GÜÇLÜ YÖNLER",
        "ÖNERİLEN İÇERİK TÜRÜ",
        "KİTLE TUTMA TAKTİKLERİ",
        "KAÇINILMASI GEREKENLER",
        "30 GÜNLÜK EYLEM PLANI",
    ],
    QueryType.AUDIENCE_RETENTION: [
        "GÜÇLÜ YÖNLER",
        "İZLEME SÜRESİ SORUNLARI",
        "YAPISAL ÖNERİLER",
        "HOOK VE TEMPO TAKTİKLERİ",
        "ÖNCE DENE",
    ],
    QueryType.GROWTH_STRATEGY: [
        "GÜÇLÜ YÖNLER",
        "BÜYÜME ENGELLERİ",
        "KANAL STRATEJİSİ",
        "ABONE KAZANIM TAKTİKLERİ",
        "30 GÜNLÜK EYLEM PLANI",
    ],
    QueryType.TOPIC_IDEAS: [
        "GÜÇLÜ YÖNLER",
        "VİDEO KONUSU ÖNERİLERİ",
        "BAŞLIK FORMÜLLERI",
        "FORMAT TAVSİYESİ",
        "ÖNCE DENE",
    ],
    QueryType.TECHNICAL_FIXES: [
        "GÜÇLÜ YÖNLER",
        "THUMBNAIL VE BAŞLIK",
        "VİDEO YAPISI",
        "CTR İYİLEŞTİRME",
        "ÖNCE DENE",
    ],
}


# ──────────────────────────────────────────────────────────────────── #
# Çıktı veri yapıları                                                    #
# ──────────────────────────────────────────────────────────────────── #

@dataclass
class StrategySection:
    heading: str
    items: list[str]           # madde listesi
    paragraph: str = ""        # liste yoksa düz metin


@dataclass
class StrategyReport:
    """
    AIStrategist'ten dönen tam strateji raporu.
    Her bölüm ayrıştırılmış ve yapılandırılmıştır.
    """
    query_type: QueryType
    period_label: str                          # "Son 30 gün"
    data_snapshot: str                         # Claude'a gönderilen veri bloğu
    question_asked: str                        # kullanılan prompt sorusu
    sections: list[StrategySection]            # ayrıştırılmış bölümler
    strengths: list[str]                       # GÜÇLÜ YÖNLER bölümü
    primary_recommendation: str               # ilk önerilen şey (1 paragraf)
    action_items: list[str]                   # eylem planı / önce dene
    raw_response: str                          # Claude'un ham yanıtı

    def to_markdown(self) -> str:
        """Raporu Markdown biçiminde döndürür."""
        lines = [
            f"# Strateji Raporu — {self.period_label}",
            f"\n> **Sorgu türü:** {self.query_type.value}",
            f"> **Soru:** {self.question_asked}\n",
            "---\n",
        ]
        for sec in self.sections:
            lines.append(f"## {sec.heading}")
            if sec.items:
                lines.extend(f"- {item}" for item in sec.items)
            elif sec.paragraph:
                lines.append(sec.paragraph)
            lines.append("")
        return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────── #
# AIStrategist                                                           #
# ──────────────────────────────────────────────────────────────────── #

class AIStrategist:
    """
    file_processor.py çıktısını Claude'a göndererek strateji raporu üretir.

    Kullanım:
        fp     = FileProcessor()
        merged = fp.load_folder("Analizler")

        strategist = AIStrategist()

        # Temel kullanım — hangi içerik türüne odaklanmalıyım?
        report = strategist.analyze(merged)

        # Belirli bir sorgu türü
        report = strategist.analyze(merged, query=QueryType.TOPIC_IDEAS)

        # Serbest soru
        report = strategist.ask(merged, "Shorts mı Long-form mu?")

        # Gerçek zamanlı akış
        for chunk in strategist.stream(merged):
            print(chunk, end="", flush=True)
    """

    def __init__(self, period_label: str = "Son 30 gün"):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError("ANTHROPIC_API_KEY ortam değişkeni tanımlı değil.")
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model  = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
        self._max_tokens = int(os.getenv("MAX_TOKENS", "4096"))
        self._period = period_label

    # ──────────────────────────────────────────── genel giriş noktaları ──

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=4, max=30))
    def analyze(
        self,
        source: FileReport | MergedReport | AggregatedStats,
        query: QueryType = QueryType.CONTENT_FOCUS,
        extra_context: str = "",
    ) -> StrategyReport:
        """
        Temel analiz metodu. Veri kaynağını alır, seçilen sorgu türüne
        göre Claude'a gönderir, yapılandırılmış StrategyReport döndürür.
        """
        stats = _extract_stats(source)
        data_block = _build_data_block(stats, self._period)
        focus_q = _QUERY_FOCUS[query]
        sections_expected = _RESPONSE_SECTIONS.get(query, _RESPONSE_SECTIONS[QueryType.CONTENT_FOCUS])

        prompt = _build_prompt(
            period=self._period,
            data_block=data_block,
            focus_question=focus_q,
            sections=sections_expected,
            extra_context=extra_context,
        )

        raw = self._call(prompt, cached_context=data_block)
        return _parse_response(
            raw=raw,
            query_type=query,
            period_label=self._period,
            data_block=data_block,
            question=focus_q,
            section_headings=sections_expected,
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=4, max=30))
    def ask(
        self,
        source: FileReport | MergedReport | AggregatedStats,
        custom_question: str,
        extra_context: str = "",
    ) -> StrategyReport:
        """
        Serbest soru sorma. Veri bloğu otomatik eklenir,
        soru kullanıcının yazdığı olur.
        """
        stats = _extract_stats(source)
        data_block = _build_data_block(stats, self._period)
        sections_expected = ["GÜÇLÜ YÖNLER", "ANALİZ", "ÖNERİLER", "ÖNCE DENE"]

        prompt = _build_prompt(
            period=self._period,
            data_block=data_block,
            focus_question=custom_question,
            sections=sections_expected,
            extra_context=extra_context,
        )

        raw = self._call(prompt, cached_context=data_block)
        return _parse_response(
            raw=raw,
            query_type=QueryType.CUSTOM,
            period_label=self._period,
            data_block=data_block,
            question=custom_question,
            section_headings=sections_expected,
        )

    def stream(
        self,
        source: FileReport | MergedReport | AggregatedStats,
        query: QueryType = QueryType.CONTENT_FOCUS,
        extra_context: str = "",
    ) -> Iterator[str]:
        """
        Strateji yanıtını gerçek zamanlı metin parçaları olarak verir.
        Uzun raporları terminale canlı yazdırmak için kullanılır.
        """
        stats = _extract_stats(source)
        data_block = _build_data_block(stats, self._period)
        focus_q = _QUERY_FOCUS[query]
        sections_expected = _RESPONSE_SECTIONS.get(query, _RESPONSE_SECTIONS[QueryType.CONTENT_FOCUS])

        prompt = _build_prompt(
            period=self._period,
            data_block=data_block,
            focus_question=focus_q,
            sections=sections_expected,
            extra_context=extra_context,
        )

        with self._client.messages.stream(
            model=self._model,
            max_tokens=self._max_tokens,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            yield from stream.text_stream

    # ─────────────────────────────────────────────────── dahili çağrı ──

    def _call(self, prompt: str, cached_context: str = "") -> str:
        """
        Veri bloğu 1000 karakterin üzerindeyse prompt caching kullanır
        — aynı veriyle birden fazla sorgu yapılıyorsa maliyet düşer.
        """
        system_blocks: list[dict] = [{"type": "text", "text": _SYSTEM_PROMPT}]

        if len(cached_context) > 1000:
            system_blocks.append({
                "type": "text",
                "text": cached_context,
                "cache_control": {"type": "ephemeral"},
            })
            user_content = prompt.replace(cached_context, "[VERİ YUKARDA]", 1).strip()
        else:
            user_content = prompt

        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system_blocks,
            messages=[{"role": "user", "content": user_content}],
        )
        return response.content[0].text


# ──────────────────────────────────────────────────────────────────── #
# Prompt inşası                                                          #
# ──────────────────────────────────────────────────────────────────── #

def _build_data_block(stats: AggregatedStats | None, period: str) -> str:
    """
    AggregatedStats → Claude'a gönderilecek okunabilir veri bloğu.
    Bu blok şablondaki [VERİ] yerine geçer.
    """
    if stats is None:
        return f"({period} için istatistik verisi bulunamadı.)"

    top_lines = "\n".join(
        f"  {i+1}. \"{v.title}\" — "
        f"{v.views:,} izlenme | CTR %{v.ctr_pct} | "
        f"Ort. izleme {v.avg_watch_fmt} | "
        f"İzleme süresi {v.watch_time_hours:.1f} saat"
        for i, v in enumerate(stats.top_performers)
    ) or "  (veri yok)"

    bot_lines = "\n".join(
        f"  {i+1}. \"{v.title}\" — "
        f"{v.views:,} izlenme | CTR %{v.ctr_pct} | "
        f"Ort. izleme {v.avg_watch_fmt}"
        for i, v in enumerate(stats.underperformers)
    ) or "  (veri yok)"

    ctr_dist = "  |  ".join(
        f"{bucket}: {count} video"
        for bucket, count in stats.ctr_distribution.items()
        if count > 0
    ) or "—"

    return dedent(f"""\
        ┌─ {period} Kanal Metrikleri ─────────────────────────────
        │ Toplam video        : {stats.total_videos}
        │ Toplam izlenme      : {stats.total_views:,}
        │ Toplam izleme süresi: {stats.total_watch_time_hours:,.1f} saat
        │ Ortalama izlenme    : {stats.avg_views:,.0f}
        │ Medyan izlenme      : {stats.median_views:,.0f}
        │ Ortalama CTR        : %{stats.avg_ctr_pct}
        │ Medyan CTR          : %{stats.median_ctr_pct}
        │ Ort. izleme süresi  : {stats.avg_watch_fmt}
        │ Toplam abone değişimi: {stats.total_subscribers_gained:+,}
        │
        │ CTR Dağılımı: {ctr_dist}
        ├─ En İyi 5 Video ────────────────────────────────────────
        {top_lines}
        ├─ En Düşük 5 Video (Gelişim Fırsatları) ─────────────────
        {bot_lines}
        └──────────────────────────────────────────────────────────\
    """)


def _build_prompt(
    period: str,
    data_block: str,
    focus_question: str,
    sections: list[str],
    extra_context: str,
) -> str:
    """
    Kullanıcının istediği şablonu uygular:
      "Kanalımın son [DÖNEM] verileri şunlar: [VERİ].
       Bu verilere dayanarak, kitleyi elimde tutmak için
       hangi içerik türüne odaklanmalıyım?"
    """
    section_instructions = "\n".join(
        f"### {heading}\n(madde listesi — en az 3 somut nokta)"
        for heading in sections
    )

    extra_block = f"\nEk Bağlam:\n{extra_context}\n" if extra_context.strip() else ""

    return dedent(f"""\
        Kanalımın {period} verileri şunlar:

        {data_block}
        {extra_block}
        {focus_question}

        Yanıtını aşağıdaki bölümlerle yapılandır.
        Her bölümün başına büyük harfli başlığı yaz (### ile):

        {section_instructions}

        ÖNEMLİ KURALLAR
        • Her öneri doğrudan yukarıdaki metrik veriye dayansın.
        • Sayıları doğru kullan; tahmin yapıyorsan belirt.
        • Güçlü yönleri gerçek rakamlarla kutla.
        • Gelişim önerilerini "fırsat" ve "potansiyel" diliyle sun.
        • Eylem önerileri somut ve bu hafta uygulanabilir olsun.
    """)


# ──────────────────────────────────────────────────────────────────── #
# Yanıt ayrıştırıcı                                                     #
# ──────────────────────────────────────────────────────────────────── #

def _parse_response(
    raw: str,
    query_type: QueryType,
    period_label: str,
    data_block: str,
    question: str,
    section_headings: list[str],
) -> StrategyReport:
    sections: list[StrategySection] = []
    strengths: list[str] = []
    primary_rec = ""
    action_items: list[str] = []

    # Her bölümü satır satır tarayarak ayıkla
    current_heading = ""
    current_lines: list[str] = []

    def _flush():
        nonlocal current_heading, current_lines
        if not current_heading:
            return
        items = []
        paragraph_lines = []
        for line in current_lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped[0] in "-*•" or (len(stripped) > 2 and stripped[0].isdigit() and stripped[1] in ".)"):
                clean = stripped.lstrip("-*•0123456789.) ").strip()
                if clean:
                    items.append(clean)
            else:
                paragraph_lines.append(stripped)

        sec = StrategySection(
            heading=current_heading,
            items=items,
            paragraph=" ".join(paragraph_lines),
        )
        sections.append(sec)
        current_heading = ""
        current_lines = []

    for line in raw.splitlines():
        # ### BAŞLIK veya ## BAŞLIK satırını yakala
        heading_match = None
        stripped = line.strip()
        if stripped.startswith("###"):
            heading_match = stripped.lstrip("#").strip()
        elif stripped.startswith("##") and not stripped.startswith("###"):
            heading_match = stripped.lstrip("#").strip()

        if heading_match:
            _flush()
            # Bilinen bölüm başlıklarıyla eşleştir (kısmi eşleşme)
            matched = next(
                (h for h in section_headings if h in heading_match.upper()),
                heading_match.upper(),
            )
            current_heading = matched
        else:
            current_lines.append(line)

    _flush()  # son bölümü kapat

    # Özel bölümleri ayıkla
    for sec in sections:
        if "GÜÇLÜ" in sec.heading:
            strengths = sec.items or [sec.paragraph]
        if any(k in sec.heading for k in ("ÖNERİLEN", "STRATEJİ", "KANAL", "ANALİZ", "VİDEO KONUSU")):
            if not primary_rec:
                primary_rec = (sec.items[0] if sec.items else sec.paragraph)
        if any(k in sec.heading for k in ("EYLEM", "ÖNCE DENE")):
            action_items = sec.items or [sec.paragraph]

    return StrategyReport(
        query_type=query_type,
        period_label=period_label,
        data_snapshot=data_block,
        question_asked=question,
        sections=sections,
        strengths=strengths,
        primary_recommendation=primary_rec,
        action_items=action_items,
        raw_response=raw,
    )


# ──────────────────────────────────────────────────────────────────── #
# Yardımcı                                                               #
# ──────────────────────────────────────────────────────────────────── #

def _extract_stats(
    source: FileReport | MergedReport | AggregatedStats,
) -> AggregatedStats | None:
    """FileReport, MergedReport veya doğrudan AggregatedStats kabul eder."""
    if isinstance(source, AggregatedStats):
        return source
    return source.stats  # FileReport ve MergedReport ikisi de .stats içeriyor
