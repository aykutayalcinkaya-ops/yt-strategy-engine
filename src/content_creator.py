"""
src/content_creator.py
~~~~~~~~~~~~~~~~~~~~~~
3-pas senaryo üretim pipeline'ı.

Pas 1 — Taslak: Konu + alan tespitine göre 12.000+ karakter derinlemesine senaryo.
Pas 2 — Doğrulama: Alan-özgü iddialar (fizik, borsa, tarih) kontrol edilir;
         hatalı/eksik bilgiler yapılandırılmış rapor olarak döner.
Pas 3 — Düzeltme: Doğrulama bulguları senaryoya işlenir; gerekiyorsa genişletme yapılır.

Çıktı: outputs/senaryolar/YYYY-MM-DD_HHMM_<slug>.md
"""

from __future__ import annotations

import os
import re
import textwrap
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator

import anthropic
from tenacity import retry, stop_after_attempt, wait_exponential

# ──────────────────────────────────────────────────────────────────── #
# Sabitler                                                              #
# ──────────────────────────────────────────────────────────────────── #

_TARGET_CHARS = 12_000
_MIN_CHARS = 11_800
_OUTPUT_ROOT = Path(os.getenv("OUTPUT_DIR", "outputs")) / "senaryolar"

# Alan anahtar kelimeleri → otomatik domain tespiti
_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "fizik": [
        "enerji", "kuvvet", "hız", "ivme", "kütle", "momentum", "termodinamik",
        "kuantum", "görelilik", "elektromanyetik", "dalga", "frekans", "basınç",
        "termal", "optik", "nükleer", "foton", "elektron", "madde", "ışık",
        "newton", "einstein", "plank", "joule", "watt",
    ],
    "borsa": [
        "borsa", "hisse", "yatırım", "portföy", "getiri", "risk", "faiz",
        "enflasyon", "döviz", "kripto", "bitcoin", "piyasa", "endeks",
        "fon", "tahvil", "bono", "temettü", "p/e", "beta", "volatilite",
        "teknik analiz", "temel analiz", "bist", "nasdaq", "s&p",
    ],
    "tarih": [
        "tarih", "savaş", "imparatorluk", "devrim", "keşif", "antik", "ortaçağ",
        "rönesans", "sanayi", "dönem", "yüzyıl", "medeniyet", "osmanlı",
        "cumhuriyet", "atatürk", "roma", "yunan", "mısır", "moğol",
        "1. dünya", "2. dünya", "soğuk savaş",
    ],
}

# ──────────────────────────────────────────────────────────────────── #
# Veri yapıları                                                         #
# ──────────────────────────────────────────────────────────────────── #

@dataclass
class SectionMetrics:
    intro_chars: int = 0
    analysis_chars: int = 0
    technical_chars: int = 0
    conclusion_chars: int = 0

    @property
    def total(self) -> int:
        return self.intro_chars + self.analysis_chars + self.technical_chars + self.conclusion_chars

    def as_dict(self) -> dict[str, int]:
        return {
            "Giriş": self.intro_chars,
            "Derinlemesine Analiz": self.analysis_chars,
            "Teknik Detaylar": self.technical_chars,
            "Sonuç": self.conclusion_chars,
        }


@dataclass
class ValidationFinding:
    domain: str          # "fizik" | "borsa" | "tarih"
    severity: str        # "hata" | "uyarı" | "öneri"
    original_claim: str
    correction: str
    line_hint: str = ""  # orijinal metinden tanımlayıcı alıntı


@dataclass
class ValidationReport:
    domains_checked: list[str]
    findings: list[ValidationFinding]
    is_clean: bool       # sıfır "hata" bulgusu varsa True

    @property
    def errors(self) -> list[ValidationFinding]:
        return [f for f in self.findings if f.severity == "hata"]

    @property
    def warnings(self) -> list[ValidationFinding]:
        return [f for f in self.findings if f.severity == "uyarı"]

    def summary(self) -> str:
        if self.is_clean:
            return "Doğrulama geçti — hata bulunamadı."
        parts = []
        if self.errors:
            parts.append(f"{len(self.errors)} hata")
        if self.warnings:
            parts.append(f"{len(self.warnings)} uyarı")
        return "Doğrulama bulguları: " + ", ".join(parts)


@dataclass
class ScenarioDraft:
    topic: str
    title: str
    domains: list[str]
    content: str
    char_count: int
    word_count: int
    section_metrics: SectionMetrics
    validation: ValidationReport
    passes_completed: int
    created_at: str
    saved_path: str = ""

    @property
    def meets_length_requirement(self) -> bool:
        return self.char_count >= _MIN_CHARS

    def quality_badge(self) -> str:
        checks = [
            self.meets_length_requirement,
            self.validation.is_clean,
            self.section_metrics.intro_chars >= 300,
            self.section_metrics.analysis_chars >= 3000,
            self.section_metrics.technical_chars >= 2000,
            self.section_metrics.conclusion_chars >= 400,
        ]
        passed = sum(checks)
        return f"{passed}/{len(checks)} kalite kontrolü geçti"


# ──────────────────────────────────────────────────────────────────── #
# Sistem promptları                                                     #
# ──────────────────────────────────────────────────────────────────── #

_SYSTEM_WRITER = textwrap.dedent("""\
    Sen, YouTube için derinlemesine teknik senaryolar yazan uzman bir içerik yazarısın.
    Türkçe yazar, ancak teknik terimleri orijinal diliyle (parantez içinde açıklamayla) kullanırsın.

    TEMEL PRENSİPLER
    ─────────────────
    Teknik Doğruluk
    • Her sayısal değer, formül ve fiziksel büyüklük gerçek ve birimiyle birlikte verilir.
    • Tarihsel olaylar doğru tarih ve kişilerle aktarılır; belirsizse "yaklaşık" ifadesi eklenir.
    • Finansal veriler ve borsa mekanizmaları gerçek piyasa mantığına uygun açıklanır.

    Objektif Analiz
    • Konu birden fazla perspektiften ele alınır; avantaj/dezavantaj dengesi korunur.
    • İddialı çıkarımlar somut veriye dayandırılır; tahminler "tahmin" olarak etiketlenir.

    Yapıcı Ton (Praising)
    • İzleyici her bölümde motive edilir; zorluklara "büyüme fırsatı" çerçevesi önerilir.
    • Başarı örnekleri canlı, ilham verici bir dille aktarılır.

    UZUNLUK KURALI: Senaryo en az 12.000, en fazla 15.000 karakter olmalıdır.
    Bölüm hedefleri:
      • Giriş (GİRİŞ başlığı): 400–700 karakter
      • Derinlemesine Analiz (DERİNLEMESİNE ANALİZ): 4.500–6.000 karakter
      • Teknik Detaylar (TEKNİK DETAYLAR): 3.500–5.000 karakter
      • Yapıcı Sonuç (YAPICI SONUÇ): 600–1.000 karakter
""")

_SYSTEM_VALIDATOR = textwrap.dedent("""\
    Sen, teknik içerik doğrulama uzmanısın. Görevin; sağlanan senaryo metninde
    fizik, finans/borsa ve tarih alanlarındaki iddiaları titizlikle denetlemek.

    ÇIKTI FORMATI — her bulgu için tam olarak şu yapıyı kullan:

    BULGU
    Alan: <fizik|borsa|tarih>
    Şiddet: <hata|uyarı|öneri>
    Orijinal İddia: <metinden doğrudan alıntı, max 120 karakter>
    Düzeltme: <doğru bilgi veya önerilen revizyon>
    Satır İpucu: <metinde aranabilecek benzersiz bir kelime/ifade>
    ───

    BULGULAR BÖLÜMÜ başlığıyla başla.
    Eğer hiçbir sorun yoksa: "BULGULAR BÖLÜMÜ\nHata bulunamadı." yaz.

    Doğrulama kuralları:
    Fizik   — birimler, formüller, büyüklük sırası, fizik yasaları
    Borsa   — piyasa mekanizmaları, finansal araç tanımları, oranlar
    Tarih   — tarihler, isimler, coğrafya, kronoloji
""")

_SYSTEM_CORRECTOR = textwrap.dedent("""\
    Sen, senaryo editörüsün. Sağlanan TASLAK metni ve DOĞRULAMA RAPORU'nu kullanarak
    hataları düzelt, uyarıları gözden geçir ve metni daha güçlü kıl.

    KURALLAR
    • Her düzeltmeyi sessizce entegre et — meta yorum ekleme.
    • Metni kısaltma; gerekiyorsa ilgili bölümü genişlet.
    • Dört bölüm başlığını (GİRİŞ, DERİNLEMESİNE ANALİZ, TEKNİK DETAYLAR, YAPICI SONUÇ) koru.
    • Çıktı yalnızca düzeltilmiş senaryo metni olsun — başka açıklama yok.
    • Hedef: en az 12.000 karakter.
""")

# ──────────────────────────────────────────────────────────────────── #
# Ana ContentCreator sınıfı                                             #
# ──────────────────────────────────────────────────────────────────── #

class ContentCreator:
    """
    Konu başlığından 3-pas pipeline ile doğrulanmış, 12.000+ karakter
    senaryo üretir ve outputs/senaryolar/ altına Markdown olarak kaydeder.

    API anahtarı ANTHROPIC_API_KEY ortam değişkeninden okunur.
    """

    def __init__(self):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError("ANTHROPIC_API_KEY ortam değişkeni tanımlı değil.")
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
        self._max_tokens = int(os.getenv("SCENARIO_MAX_TOKENS", "16000"))

    # ─────────────────────────────────────────────────── public API ── #

    def create(
        self,
        topic: str,
        title: str = "",
        extra_instructions: str = "",
        auto_save: bool = True,
    ) -> ScenarioDraft:
        """
        Tam pipeline'ı çalıştırır:
          1. Taslak üretimi
          2. Alan doğrulaması
          3. Düzeltme entegrasyonu + gerekiyorsa genişletme

        Returns ScenarioDraft — saved_path alanı dolu ise dosya kaydedildi.
        """
        title = title or topic
        domains = _detect_domains(topic + " " + title)

        # ── Pas 1: Taslak ──────────────────────────────────────────── #
        draft_text = self._pass_draft(topic, title, domains, extra_instructions)

        # ── Pas 2: Alan doğrulaması ─────────────────────────────────── #
        validation = ValidationReport(
            domains_checked=[], findings=[], is_clean=True
        )
        if domains:
            validation = self._pass_validate(draft_text, domains)

        # ── Pas 3: Düzeltme + uzunluk garantisi ────────────────────── #
        final_text, passes = self._pass_correct(
            draft_text, validation, topic, title
        )

        sections = _measure_sections(final_text)
        draft = ScenarioDraft(
            topic=topic,
            title=title,
            domains=domains,
            content=final_text,
            char_count=len(final_text),
            word_count=len(final_text.split()),
            section_metrics=sections,
            validation=validation,
            passes_completed=passes,
            created_at=datetime.now().isoformat(),
        )

        if auto_save:
            draft.saved_path = self.save(draft)

        return draft

    def stream_create(
        self,
        topic: str,
        title: str = "",
        extra_instructions: str = "",
    ) -> Iterator[str]:
        """
        Sadece Pas 1'i (taslak üretimini) akış olarak verir.
        Uzun içeriklerde gerçek zamanlı terminal çıktısı için kullanılır.
        Doğrulama/düzeltme pasları stream sonrası ayrıca çalıştırılmalıdır.
        """
        title = title or topic
        domains = _detect_domains(topic + " " + title)
        prompt = _build_draft_prompt(topic, title, domains, "")
        with self._client.messages.stream(
            model=self._model,
            max_tokens=self._max_tokens,
            system=_SYSTEM_WRITER,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            yield from stream.text_stream

    def save(self, draft: ScenarioDraft) -> str:
        """Senaryoyu outputs/senaryolar/ altına tarihli Markdown dosyası olarak kaydeder."""
        _OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        slug = _slugify(draft.title)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
        path = _OUTPUT_ROOT / f"{timestamp}_{slug}.md"
        path.write_text(_render_markdown(draft), encoding="utf-8")
        return str(path)

    # ──────────────────────────────────────────────── pas metodları ── #

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=4, max=30))
    def _pass_draft(
        self,
        topic: str,
        title: str,
        domains: list[str],
        extra: str,
    ) -> str:
        prompt = _build_draft_prompt(topic, title, domains, extra)
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=_SYSTEM_WRITER,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=4, max=30))
    def _pass_validate(self, text: str, domains: list[str]) -> ValidationReport:
        domain_str = ", ".join(domains)
        prompt = textwrap.dedent(f"""\
            Aşağıdaki senaryoyu {domain_str} alanları açısından doğrula.
            Yalnızca gerçekten bulduğun sorunları raporla.

            SENARYO:
            {text}
        """)
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=4096,
            system=_SYSTEM_VALIDATOR,
            messages=[{"role": "user", "content": prompt}],
        )
        return _parse_validation_report(resp.content[0].text, domains)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=4, max=30))
    def _pass_correct(
        self,
        draft: str,
        validation: ValidationReport,
        topic: str,
        title: str,
    ) -> tuple[str, int]:
        """
        Doğrulama bulgularını entegre eder.
        Sonuç hâlâ _MIN_CHARS altındaysa tek bir genişletme pası yapar.
        Tamamlanan pas sayısını döndürür.
        """
        passes = 2  # draft + validate

        if validation.is_clean and len(draft) >= _MIN_CHARS:
            return draft, passes

        finding_block = _format_findings_for_prompt(validation)
        char_deficit = max(0, _TARGET_CHARS - len(draft))

        correction_instruction = textwrap.dedent(f"""\
            TASLAK:
            {draft}

            DOĞRULAMA RAPORU:
            {finding_block}

            {'Ayrıca metin ' + str(char_deficit) + ' karakter kısa — ilgili bölümleri genişlet.' if char_deficit > 0 else ''}
            Düzeltilmiş ve eksiksiz senaryoyu yaz:
        """)

        resp = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=_SYSTEM_CORRECTOR,
            messages=[{"role": "user", "content": correction_instruction}],
        )
        corrected = resp.content[0].text
        passes += 1

        # Son uzunluk garantisi — hâlâ kısaysa hedefli genişletme
        if len(corrected) < _MIN_CHARS:
            corrected = self._expand(corrected, topic, title, _TARGET_CHARS - len(corrected))
            passes += 1

        return corrected, passes

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=4, max=20))
    def _expand(self, text: str, topic: str, title: str, chars_needed: int) -> str:
        prompt = textwrap.dedent(f"""\
            Konu: {topic}
            Başlık: {title}

            Aşağıdaki senaryo yaklaşık {chars_needed} karakter kısa.
            Teknik detaylar, somut örnekler ve vaka çalışmaları ekleyerek genişlet.
            Dört bölüm başlığını koru. Tüm metni eksiksiz yaz:

            {text}
        """)
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=_SYSTEM_WRITER,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text


# ──────────────────────────────────────────────────────────────────── #
# Yardımcı fonksiyonlar                                                 #
# ──────────────────────────────────────────────────────────────────── #

def _detect_domains(text: str) -> list[str]:
    """Metindeki anahtar kelimelerden alan listesi çıkarır."""
    lower = text.lower()
    return [
        domain
        for domain, keywords in _DOMAIN_KEYWORDS.items()
        if any(kw in lower for kw in keywords)
    ]


def _build_draft_prompt(
    topic: str,
    title: str,
    domains: list[str],
    extra: str,
) -> str:
    domain_instructions = ""
    if "fizik" in domains:
        domain_instructions += textwrap.dedent("""\
            FİZİK KISITLARI
            • Tüm fiziksel büyüklüklere SI birimi ekle (joule, watt, newton, metre/saniye vb.).
            • Formülleri yazarken sembolik formu ve sözel açıklamasını birlikte ver.
            • Termodinamik yasalarına, enerji korunumuna ve nedensellik ilkesine aykırı ifade kullanma.
        """)
    if "borsa" in domains:
        domain_instructions += textwrap.dedent("""\
            BORSA/FİNANS KISITLARI
            • Getiri tahminleri için "geçmiş performans geleceği garanti etmez" uyarısı ekle.
            • P/E oranı, beta, volatilite gibi kavramlar doğru formül ve yorumla verilsin.
            • Piyasa mekanizmaları (arz/talep, likidite, marj) doğru çerçevelensin.
        """)
    if "tarih" in domains:
        domain_instructions += textwrap.dedent("""\
            TARİH KISITLARI
            • Olayları net tarihlerle ver; tarih belirsizse "yaklaşık MÖ/MS X" kullan.
            • Kişi isimlerini ve unvanlarını doğru ve tam yaz.
            • Kronolojik tutarsızlık yaratma; nedensellik zincirini koru.
        """)

    extra_block = f"\nEK TALİMATLAR\n{extra}\n" if extra.strip() else ""

    return textwrap.dedent(f"""\
        KONU    : {topic}
        BAŞLIK  : {title}
        {extra_block}
        {domain_instructions}
        Aşağıdaki dört bölümden oluşan, en az 12.000 karakterlik bir YouTube senaryosu yaz.
        Her bölümü büyük harfli başlıkla işaretle:

        ## GİRİŞ
        (400–700 karakter)
        • Güçlü merak kancası — izleyiciyi ilk 15 saniyede yakala.
        • Bu videodan ne öğrenileceği açıkça belirtilsin.
        • Kanala abone olmaya yapıcı bir davet.

        ## DERİNLEMESİNE ANALİZ
        (4.500–6.000 karakter)
        • Konunun tarihsel/kavramsal arka planı.
        • 3–5 alt başlıkla yapılandırılmış, veriye dayalı analiz.
        • Gerçek hayat örnekleri ve vaka çalışmaları.
        • Rakip görüşlerin dengeli karşılaştırması.

        ## TEKNİK DETAYLAR
        (3.500–5.000 karakter)
        • Adım adım numaralandırılmış süreç veya formüller.
        • Tablolar, listeler ve somut sayısal değerler.
        • Yaygın hatalar ve önleme yöntemleri.
        • İleri düzey okuyucu/izleyici için derinlik katmanı.

        ## YAPICI SONUÇ
        (600–1.000 karakter)
        • Ana bulguların özeti (madde madde).
        • İzleyiciyi harekete geçirecek net bir eylem çağrısı.
        • Motive edici kapanış cümlesi.
        • Bir sonraki video teaserı ve abone/beğeni daveti.

        UYARI: Tüm bölümleri eksiksiz yaz. Toplam karakter sayısı 12.000'in altına düşmesin.
    """)


def _parse_validation_report(raw: str, domains: list[str]) -> ValidationReport:
    """Claude'un doğrulama yanıtını ValidationReport'a dönüştürür."""
    findings: list[ValidationFinding] = []

    if "hata bulunamadı" in raw.lower():
        return ValidationReport(domains_checked=domains, findings=[], is_clean=True)

    # Her BULGU bloğunu ayrıştır
    blocks = re.split(r"BULGU\s*\n", raw, flags=re.IGNORECASE)
    for block in blocks[1:]:  # ilk parça başlık öncesi metin
        def _field(key: str) -> str:
            m = re.search(rf"{key}\s*:\s*(.+)", block, re.IGNORECASE)
            return m.group(1).strip() if m else ""

        domain = _field("Alan").lower()
        severity = _field("Şiddet").lower()
        if severity not in ("hata", "uyarı", "öneri"):
            severity = "uyarı"
        claim = _field("Orijinal İddia")
        correction = _field("Düzeltme")
        hint = _field("Satır İpucu")

        if claim or correction:
            findings.append(ValidationFinding(
                domain=domain,
                severity=severity,
                original_claim=claim,
                correction=correction,
                line_hint=hint,
            ))

    has_errors = any(f.severity == "hata" for f in findings)
    return ValidationReport(
        domains_checked=domains,
        findings=findings,
        is_clean=not has_errors,
    )


def _format_findings_for_prompt(report: ValidationReport) -> str:
    if report.is_clean and not report.findings:
        return "Doğrulama bulgusuz geçti."
    lines = []
    for f in report.findings:
        lines.append(
            f"[{f.severity.upper()}] ({f.domain}) "
            f"Orijinal: '{f.original_claim[:80]}' → Düzeltme: {f.correction}"
        )
    return "\n".join(lines)


def _measure_sections(text: str) -> SectionMetrics:
    """Bölüm başlıklarını bulup her bölümün karakter sayısını ölçer."""
    anchors = {
        "GİRİŞ": "intro",
        "DERİNLEMESİNE ANALİZ": "analysis",
        "TEKNİK DETAYLAR": "technical",
        "YAPICI SONUÇ": "conclusion",
    }
    positions: list[tuple[int, str]] = []
    for heading, key in anchors.items():
        m = re.search(rf"##\s*{heading}", text, re.IGNORECASE)
        if m:
            positions.append((m.start(), key))
    positions.sort(key=lambda x: x[0])

    metrics = SectionMetrics()
    for i, (pos, key) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        length = end - pos
        setattr(metrics, f"{key}_chars", length)
    return metrics


def _slugify(title: str, max_len: int = 50) -> str:
    slug = title.lower()
    slug = re.sub(r"[şŞ]", "s", slug)
    slug = re.sub(r"[çÇ]", "c", slug)
    slug = re.sub(r"[ğĞ]", "g", slug)
    slug = re.sub(r"[üÜ]", "u", slug)
    slug = re.sub(r"[öÖ]", "o", slug)
    slug = re.sub(r"[ıİ]", "i", slug)
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    slug = slug.strip("_")
    return slug[:max_len]


def _render_markdown(draft: ScenarioDraft) -> str:
    """ScenarioDraft'ı zengin front-matter içeren Markdown'a dönüştürür."""
    val = draft.validation
    findings_md = ""
    if val.findings:
        rows = "\n".join(
            f"| {f.domain} | {f.severity} | {f.original_claim[:60]} | {f.correction[:80]} |"
            for f in val.findings
        )
        findings_md = textwrap.dedent(f"""\
            ## Doğrulama Bulguları

            | Alan | Şiddet | Orijinal İddia | Düzeltme |
            |------|--------|----------------|----------|
            {rows}

        """)

    section_rows = "\n".join(
        f"| {name} | {chars:,} |"
        for name, chars in draft.section_metrics.as_dict().items()
    )

    domain_badge = ", ".join(draft.domains) if draft.domains else "genel"

    return textwrap.dedent(f"""\
        ---
        başlık: "{draft.title}"
        konu: "{draft.topic}"
        alanlar: {domain_badge}
        karakter: {draft.char_count:,}
        kelime: {draft.word_count:,}
        oluşturulma: {draft.created_at}
        kalite: {draft.quality_badge()}
        doğrulama: {val.summary()}
        paslar: {draft.passes_completed}
        ---

        # {draft.title}

        > **Konu:** {draft.topic}
        > **Alan(lar):** {domain_badge}
        > **Kalite:** {draft.quality_badge()}
        > **Karakter:** {draft.char_count:,} | **Kelime:** {draft.word_count:,}

        ## Bölüm Metrikleri

        | Bölüm | Karakter |
        |-------|----------|
        {section_rows}

        {findings_md}---

        {draft.content}
    """).strip()
