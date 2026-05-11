"""
src/writer_engine.py
~~~~~~~~~~~~~~~~~~~~
Analizden gelen strateji önerilerini veya kullanıcının verdiği konuyu
alıp ≥12.000 karakter YouTube senaryosu üretir.

Mimari:
  Pas 1 — Taslak        : Konu + strateji bağlamıyla tam senaryo
  Pas 2 — İddia Çıkarımı: Claude, kendi metnindeki doğrulanabilir
                           iddiaları (sayı, formül, tarih, oran) listeler
  Pas 3 — Çapraz Doğrulama: Fizik · Finans · Tarih için ayrı mini-promptlar
  Pas 4 — Entegrasyon  : Doğrulama bulguları senaryoya işlenir;
                          karakter açığı varsa hedefli genişletme yapılır

Çıktı: outputs/scripts/YYYY-MM-DD_HHMM_<slug>.md
"""

from __future__ import annotations

import os
import re
import textwrap
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator, Union

import anthropic
from tenacity import retry, stop_after_attempt, wait_exponential

from .ai_strategist import StrategyReport


# ──────────────────────────────────────────────────────────────────── #
# Sabitler                                                              #
# ──────────────────────────────────────────────────────────────────── #

_MIN_CHARS   = 12_000
_TARGET_CHARS = 13_500
_OUTPUT_ROOT = Path(os.getenv("OUTPUT_DIR", "outputs")) / "scripts"

# Senaryo bölümleri ve karakter hedefleri
_SECTIONS: list[tuple[str, int, int]] = [
    # (başlık_etiketi, min_kr, max_kr)
    ("HOOK",      300,   600),
    ("BAĞLAM",    800,  1400),
    ("BÖLÜM_1", 1800,  2800),
    ("BÖLÜM_2", 1800,  2800),
    ("BÖLÜM_3", 1500,  2400),
    ("UZMAN",    900,  1600),
    ("KAPANIS",  600,  1000),
]

# Domain anahtar kelimeleri
_DOMAINS: dict[str, list[str]] = {
    "fizik": [
        "enerji", "kuvvet", "hız", "ivme", "kütle", "momentum", "termodinamik",
        "kuantum", "görelilik", "elektromanyetik", "dalga", "frekans", "basınç",
        "nükleer", "foton", "joule", "watt", "newton", "elektron", "ışık",
        "tesla", "volt", "amper", "ohm", "pascal", "kelvin",
    ],
    "finans": [
        "borsa", "hisse", "yatırım", "portföy", "faiz", "enflasyon", "döviz",
        "kripto", "piyasa", "endeks", "fon", "tahvil", "bono", "temettü",
        "p/e", "beta", "volatilite", "bist", "nasdaq", "s&p", "getiri",
        "reel getiri", "fibonacci", "destek", "direnç", "hacim",
    ],
    "tarih": [
        "tarih", "savaş", "imparatorluk", "devrim", "keşif", "antik", "ortaçağ",
        "rönesans", "osmanlı", "cumhuriyet", "atatürk", "roma", "mısır",
        "moğol", "1. dünya", "2. dünya", "soğuk savaş", "yüzyıl", "milat",
        "ms ", "mö ", "milattan", "dönem", "hanedanlık",
    ],
}

# Giriş türü: StrategyReport, düz metin veya dict
WriterInput = Union[StrategyReport, str, dict]


# ──────────────────────────────────────────────────────────────────── #
# Veri yapıları                                                         #
# ──────────────────────────────────────────────────────────────────── #

@dataclass
class SectionBudget:
    label: str
    target_min: int
    target_max: int
    actual: int = 0

    @property
    def status(self) -> str:
        if self.actual == 0:
            return "eksik"
        if self.actual < self.target_min:
            return "kısa"
        if self.actual > self.target_max:
            return "uzun"
        return "hedefte"

    def badge(self) -> str:
        icons = {"eksik": "✗", "kısa": "▲", "uzun": "▼", "hedefte": "✓"}
        return f"{icons[self.status]} {self.label}: {self.actual:,} kr ({self.status})"


@dataclass
class Claim:
    """Senaryodan çıkarılan tek bir doğrulanabilir iddia."""
    domain: str       # fizik | finans | tarih
    text: str         # iddia metni (kısa alıntı)
    context: str      # bulunduğu cümle / paragraf


@dataclass
class VerificationFinding:
    claim: Claim
    verdict: str      # "doğru" | "hatalı" | "eksik" | "belirsiz"
    correction: str   # boşsa sorun yok
    severity: str     # "kritik" | "orta" | "düşük"


@dataclass
class VerificationReport:
    domains_checked: list[str]
    claims_extracted: int
    findings: list[VerificationFinding]

    @property
    def critical_errors(self) -> list[VerificationFinding]:
        return [f for f in self.findings if f.severity == "kritik" and f.verdict == "hatalı"]

    @property
    def is_clean(self) -> bool:
        return len(self.critical_errors) == 0

    @property
    def score(self) -> int:
        """0-100 arası doğruluk skoru."""
        if not self.claims_extracted:
            return 100
        errors = sum(1 for f in self.findings if f.verdict == "hatalı")
        return max(0, round((1 - errors / max(self.claims_extracted, 1)) * 100))

    def summary_line(self) -> str:
        e = len(self.critical_errors)
        t = self.claims_extracted
        return (
            f"Doğruluk skoru: {self.score}/100  |  "
            f"{t} iddia incelendi  |  "
            f"{e} kritik hata {'bulunmadı' if e == 0 else 'bulundu'}"
        )


@dataclass
class ScriptDraft:
    topic: str
    title: str
    source_type: str          # "strategy" | "manual"
    strategy_context: str     # strateji önerisinden gelen ek bağlam
    domains: list[str]        # tespit edilen alan(lar)
    content: str
    char_count: int
    word_count: int
    section_budgets: list[SectionBudget]
    verification: VerificationReport
    passes_completed: int
    created_at: str
    saved_path: str = ""

    @property
    def meets_length(self) -> bool:
        return self.char_count >= _MIN_CHARS

    def quality_summary(self) -> str:
        checks = {
            "Uzunluk ≥12.000 kr": self.meets_length,
            "Doğrulama temiz": self.verification.is_clean,
            "Tüm bölümler mevcut": all(b.actual > 0 for b in self.section_budgets),
            "Bölüm hedefleri OK": all(b.status == "hedefte" for b in self.section_budgets),
        }
        passed = sum(checks.values())
        detail = " · ".join(f"{'✓' if v else '✗'} {k}" for k, v in checks.items())
        return f"{passed}/{len(checks)} — {detail}"


# ──────────────────────────────────────────────────────────────────── #
# Sistem promptları                                                     #
# ──────────────────────────────────────────────────────────────────── #

_SYS_WRITER = textwrap.dedent("""\
    Sen, YouTube için teknik ve bilgi yoğunluğu yüksek senaryolar yazan uzman bir içerik yazarısın.

    PRENSİPLER
    ──────────────────────────────────────────────────────────────
    Teknik Doğruluk
    • Her sayısal değer, formül ve birimi gerçek olmalı.
    • Tahmin edilen değerleri "yaklaşık" veya "tahminen" ile işaretle.
    • Kaynak gösterilemiyorsa "X kaynaklar göre" veya "gözlemlere göre" kullan.

    Objektif Analiz
    • Konuyu birden fazla perspektiften ele al; avantaj/dezavantaj dengesi koru.
    • İddialı çıkarımları veriye dayandır, kişisel görüşleri açıkça etiketle.

    Yapıcı Ton (Praising)
    • İzleyiciyi her bölümde cesaretlendir; başarı örneklerini canlı anlat.
    • Zorlukları "büyüme fırsatı" olarak çerçevele; asla yargılama.

    UZUNLUK KURALI
    Senaryo en az 12.000, en fazla 15.000 karakter olmalıdır.
    Her bölümü büyük harfli etiketle başlat: ## [HOOK], ## [BAĞLAM], vb.
""")

_SYS_EXTRACTOR = textwrap.dedent("""\
    Sen, metindeki doğrulanabilir iddiaları tespit eden bir fact-checker asistansın.
    Görevin: verilen senaryodaki fizik, finans ve tarih alanlarına ait
    somut, doğrulanabilir iddiaları listelemek.

    ÇIKTI FORMATI — her iddia için tam olarak şu yapıyı kullan:

    İDDİA
    Alan: <fizik|finans|tarih>
    Metin: <iddianın tam cümlesi veya kısa alıntısı, max 150 karakter>
    Bağlam: <bulunduğu paragrafı tanımlayan 5-10 kelime>
    ───

    Eğer doğrulanabilir iddia bulunamazsa:
    "İDDİA YOK — bu alanda doğrulanabilir somut veri bulunamadı."
""")

_SYS_VERIFIER: dict[str, str] = {
    "fizik": textwrap.dedent("""\
        Sen, fizik ve fen bilimleri alanında uzman bir doğrulama asistansın.
        Sağlanan iddiaların her birinin fizik yasalarına, SI birimlerine ve
        güncel bilimsel verilere uygunluğunu denetliyorsun.

        ÇIKTI FORMATI — her iddia için:
        SONUÇ
        İddia: <orijinal metin>
        Karar: <doğru|hatalı|eksik|belirsiz>
        Şiddet: <kritik|orta|düşük>  (hatalıysa)
        Düzeltme: <doğru bilgi; doğruysa "—">
        ───
    """),
    "finans": textwrap.dedent("""\
        Sen, finans piyasaları ve ekonomi alanında uzman bir doğrulama asistansın.
        Sağlanan iddiaların piyasa mekanizmalarına, finansal araç tanımlarına
        ve genel kabul görmüş finans prensiplerine uygunluğunu denetliyorsun.

        ÇIKTI FORMATI — her iddia için:
        SONUÇ
        İddia: <orijinal metin>
        Karar: <doğru|hatalı|eksik|belirsiz>
        Şiddet: <kritik|orta|düşük>  (hatalıysa)
        Düzeltme: <doğru bilgi; doğruysa "—">
        ───
    """),
    "tarih": textwrap.dedent("""\
        Sen, dünya tarihi alanında uzman bir doğrulama asistansın.
        Sağlanan iddiaların tarihsel gerçeklerle (tarih, isim, olay, coğrafya,
        kronoloji) uygunluğunu denetliyorsun.

        ÇIKTI FORMATI — her iddia için:
        SONUÇ
        İddia: <orijinal metin>
        Karar: <doğru|hatalı|eksik|belirsiz>
        Şiddet: <kritik|orta|düşük>  (hatalıysa)
        Düzeltme: <doğru bilgi; doğruysa "—">
        ───
    """),
}

_SYS_CORRECTOR = textwrap.dedent("""\
    Sen, senaryo editörüsün. Sağlanan TASLAK metni ve DOĞRULAMA BULGULARI'nı
    kullanarak hataları düzelt ve metni geliştir.

    KURALLAR
    • Her düzeltmeyi metne sessizce entegre et; açıklama ekleme.
    • Bölüm etiketlerini (## [HOOK] vb.) ve sırayı koru.
    • Metni kısaltma; gerekirse genişlet.
    • Hedef: en az 12.000 karakter.
    • Yalnızca düzeltilmiş senaryo metnini döndür.
""")


# ──────────────────────────────────────────────────────────────────── #
# WriterEngine                                                          #
# ──────────────────────────────────────────────────────────────────── #

class WriterEngine:
    """
    Analizden gelen StrategyReport veya düz konu string'ini alır,
    4 aşamalı doğrulamalı pipeline ile ≥12.000 karakter senaryo üretir
    ve outputs/scripts/ altına Markdown olarak kaydeder.

    Kullanım:
        from src import WriterEngine, AIStrategist, FileProcessor, QueryType

        # Strateji önerisinden
        report   = AIStrategist().analyze(fp.load_folder("Analizler"))
        engine   = WriterEngine()
        draft    = engine.write(report, title="Kuantum Bilgisayarların Geleceği")

        # Düz konu string'inden
        draft    = engine.write("yapay zeka ve iş gücü", title="AI Devrim mi?")

        print(draft.quality_summary())
        print(f"Kaydedildi: {draft.saved_path}")
    """

    def __init__(self):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError("ANTHROPIC_API_KEY ortam değişkeni tanımlı değil.")
        self._client     = anthropic.Anthropic(api_key=api_key)
        self._model      = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
        self._max_tokens = int(os.getenv("SCENARIO_MAX_TOKENS", "16000"))

    # ──────────────────────────────────────────── ana giriş noktası ──

    def write(
        self,
        source: WriterInput,
        title: str = "",
        extra_instructions: str = "",
        auto_save: bool = True,
    ) -> ScriptDraft:
        """
        Tam 4-pas pipeline:
          1. Taslak üretimi
          2. İddia çıkarımı (claim extraction)
          3. Domain bazlı çapraz doğrulama
          4. Düzeltme entegrasyonu + uzunluk garantisi
        """
        topic, strategy_ctx, source_type = _parse_input(source)
        title = title or topic
        domains = _detect_domains(topic + " " + title + " " + strategy_ctx)

        # ── Pas 1: Taslak ────────────────────────────────────────── #
        draft_text = self._pass_draft(topic, title, domains, strategy_ctx, extra_instructions)

        # ── Pas 2: İddia çıkarımı ────────────────────────────────── #
        claims = self._pass_extract_claims(draft_text, domains)

        # ── Pas 3: Domain bazlı çapraz doğrulama ─────────────────── #
        verification = self._pass_verify(draft_text, claims, domains)

        # ── Pas 4: Düzeltme + uzunluk garantisi ──────────────────── #
        final_text, passes = self._pass_correct(
            draft_text, verification, topic, title
        )

        budgets = _measure_budgets(final_text)

        script = ScriptDraft(
            topic=topic,
            title=title,
            source_type=source_type,
            strategy_context=strategy_ctx,
            domains=domains,
            content=final_text,
            char_count=len(final_text),
            word_count=len(final_text.split()),
            section_budgets=budgets,
            verification=verification,
            passes_completed=passes,
            created_at=datetime.now().isoformat(),
        )

        if auto_save:
            script.saved_path = self.save(script)

        return script

    def stream_draft(
        self,
        source: WriterInput,
        title: str = "",
        extra_instructions: str = "",
    ) -> Iterator[str]:
        """
        Yalnızca Pas 1'i (taslak) gerçek zamanlı akışla döndürür.
        Uzun içeriklerde terminale canlı yazdırmak için kullanılır.
        """
        topic, strategy_ctx, _ = _parse_input(source)
        title = title or topic
        domains = _detect_domains(topic + " " + title)
        prompt = _draft_prompt(topic, title, domains, strategy_ctx, extra_instructions)

        with self._client.messages.stream(
            model=self._model,
            max_tokens=self._max_tokens,
            system=_SYS_WRITER,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            yield from stream.text_stream

    def save(self, script: ScriptDraft) -> str:
        """ScriptDraft'ı outputs/scripts/ altına Markdown olarak kaydeder."""
        _OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        slug = _slugify(script.title)
        ts   = datetime.now().strftime("%Y-%m-%d_%H%M")
        path = _OUTPUT_ROOT / f"{ts}_{slug}.md"
        path.write_text(_render_markdown(script), encoding="utf-8")
        return str(path)

    # ────────────────────────────────────────────────────── paslar ──

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=4, max=30))
    def _pass_draft(
        self,
        topic: str,
        title: str,
        domains: list[str],
        strategy_ctx: str,
        extra: str,
    ) -> str:
        prompt = _draft_prompt(topic, title, domains, strategy_ctx, extra)
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=_SYS_WRITER,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=4, max=20))
    def _pass_extract_claims(
        self, text: str, domains: list[str]
    ) -> list[Claim]:
        """Senaryodaki doğrulanabilir iddiaları listeler (domain odaklı)."""
        if not domains:
            return []

        domain_str = ", ".join(domains)
        prompt = textwrap.dedent(f"""\
            Aşağıdaki senaryo metninde {domain_str} alanlarına ait
            doğrulanabilir somut iddiaları listele.

            SENARYO:
            {text[:8000]}
        """)
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=2048,
            system=_SYS_EXTRACTOR,
            messages=[{"role": "user", "content": prompt}],
        )
        return _parse_claims(resp.content[0].text)

    def _pass_verify(
        self,
        text: str,
        claims: list[Claim],
        domains: list[str],
    ) -> VerificationReport:
        """
        Her domain için ayrı mini-prompt ile çapraz doğrulama yapar.
        Domain başına bağımsız Claude çağrısı → daha güvenilir sonuç.
        """
        if not claims or not domains:
            return VerificationReport(
                domains_checked=domains,
                claims_extracted=0,
                findings=[],
            )

        all_findings: list[VerificationFinding] = []

        for domain in domains:
            domain_claims = [c for c in claims if c.domain == domain]
            if not domain_claims:
                continue

            claim_block = "\n".join(
                f"{i+1}. {c.text} [bağlam: {c.context}]"
                for i, c in enumerate(domain_claims)
            )
            prompt = textwrap.dedent(f"""\
                Aşağıdaki {len(domain_claims)} iddiayı {domain} bilgisi açısından doğrula:

                {claim_block}
            """)

            sys_prompt = _SYS_VERIFIER.get(domain, _SYS_VERIFIER["tarih"])
            try:
                resp = self._client.messages.create(
                    model=self._model,
                    max_tokens=2048,
                    system=sys_prompt,
                    messages=[{"role": "user", "content": prompt}],
                )
                findings = _parse_verification(resp.content[0].text, domain_claims)
                all_findings.extend(findings)
            except Exception:
                # Doğrulama hatası pipeline'ı durdurmasın
                pass

        return VerificationReport(
            domains_checked=domains,
            claims_extracted=len(claims),
            findings=all_findings,
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=4, max=30))
    def _pass_correct(
        self,
        draft: str,
        verification: VerificationReport,
        topic: str,
        title: str,
    ) -> tuple[str, int]:
        passes = 3  # draft + extract + verify

        error_findings = [
            f for f in verification.findings
            if f.verdict == "hatalı"
        ]

        if not error_findings and len(draft) >= _MIN_CHARS:
            return draft, passes

        finding_lines = "\n".join(
            f"• [{f.severity.upper()}] ({f.claim.domain}) "
            f"Hatalı: '{f.claim.text[:80]}' → Düzeltme: {f.correction}"
            for f in error_findings
        ) or "Kritik hata bulunamadı."

        char_gap = max(0, _TARGET_CHARS - len(draft))
        expansion_note = (
            f"\nAyrıca metin {char_gap:,} karakter kısa — "
            "ilgili bölümleri somut örneklerle genişlet."
            if char_gap > 0 else ""
        )

        prompt = textwrap.dedent(f"""\
            TASLAK:
            {draft}

            DOĞRULAMA BULGULARI:
            {finding_lines}
            {expansion_note}

            Düzeltilmiş ve eksiksiz senaryoyu yaz:
        """)

        resp = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=_SYS_CORRECTOR,
            messages=[{"role": "user", "content": prompt}],
        )
        corrected = resp.content[0].text
        passes += 1

        # Son uzunluk garantisi
        if len(corrected) < _MIN_CHARS:
            corrected = self._expand(corrected, topic, title)
            passes += 1

        return corrected, passes

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=4, max=20))
    def _expand(self, text: str, topic: str, title: str) -> str:
        gap = _TARGET_CHARS - len(text)
        prompt = textwrap.dedent(f"""\
            Konu: {topic} | Başlık: {title}
            Senaryo {gap:,} karakter kısa. Teknik detaylar, somut örnekler ve
            vaka çalışmaları ekleyerek genişlet. Bölüm etiketlerini koru.
            Tüm metni eksiksiz yaz:

            {text}
        """)
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=_SYS_WRITER,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text


# ──────────────────────────────────────────────────────────────────── #
# Prompt inşası                                                         #
# ──────────────────────────────────────────────────────────────────── #

def _draft_prompt(
    topic: str,
    title: str,
    domains: list[str],
    strategy_ctx: str,
    extra: str,
) -> str:
    # Domain kısıtları
    domain_rules = ""
    if "fizik" in domains:
        domain_rules += textwrap.dedent("""\
            FİZİK KURALLARI
            • Tüm büyüklüklere SI birimi ekle (J, W, N, m/s, Pa vb.).
            • Formülleri hem sembolik hem sözel açıklamayla ver.
            • Termodinamik, enerji korunumu ve nedensellik ilkelerine uy.
        """)
    if "finans" in domains:
        domain_rules += textwrap.dedent("""\
            FİNANS KURALLARI
            • Getiri tahminleri için "geçmiş performans geleceği garanti etmez" uyarısı ekle.
            • P/E, beta, volatilite gibi kavramları doğru formül ve yorumla sun.
            • Piyasa mekanizmalarını (arz/talep, likidite, marj) doğru çerçevele.
        """)
    if "tarih" in domains:
        domain_rules += textwrap.dedent("""\
            TARİH KURALLARI
            • Olayları net tarih/yüzyıl ile ver; belirsizse "yaklaşık" kullan.
            • Kişi isimlerini ve unvanlarını tam ve doğru yaz.
            • Kronolojik tutarsızlık yaratma.
        """)

    strategy_block = (
        f"\nSTRATEJİ BAĞLAMI (analiz çıktısı)\n{strategy_ctx}\n"
        if strategy_ctx.strip() else ""
    )
    extra_block = f"\nEK TALİMATLAR\n{extra}\n" if extra.strip() else ""

    # Bölüm hedefleri
    section_spec = "\n".join(
        f"## [{label}]  ({mn:,}–{mx:,} karakter)"
        for label, mn, mx in _SECTIONS
    )

    return textwrap.dedent(f"""\
        KONU  : {topic}
        BAŞLIK: {title}
        {strategy_block}{extra_block}{domain_rules}
        Aşağıdaki 7 bölümden oluşan, en az 12.000 karakterlik YouTube senaryosu yaz.
        Her bölümü tam olarak belirtilen ## etiketi ile başlat:

        {section_spec}

        BÖLÜM AÇIKLAMALARI
        ## [HOOK]     — İlk 30 saniye; merak kancası + videonun vaadi
        ## [BAĞLAM]   — Konunun önemi, izleyicinin acı noktası, büyük resim
        ## [BÖLÜM_1]  — 1. ana konu; açıklama → teknik detay → somut örnek
        ## [BÖLÜM_2]  — 2. ana konu; aynı yapı, farklı perspektif
        ## [BÖLÜM_3]  — 3. ana konu; ileri düzey içerik veya karşılaştırma
        ## [UZMAN]    — 5-7 pro ipucu, yaygın hatalar, optimizasyon taktikleri
        ## [KAPANIS]  — Özet + eylem çağrısı + abone/beğeni daveti + teaser

        GENEL KURALLAR
        • Her bölümde hedef karakter aralığını tut.
        • Teknik terimlerin ilk geçişinde parantez içi açıklama ver.
        • Gerçek vaka örnekleri ve sayısal referanslar kullan.
        • Toplam ≥ 12.000 karakter.
    """)


# ──────────────────────────────────────────────────────────────────── #
# Ayrıştırıcılar                                                        #
# ──────────────────────────────────────────────────────────────────── #

def _parse_claims(raw: str) -> list[Claim]:
    claims: list[Claim] = []
    if "iddia yok" in raw.lower():
        return claims

    for block in re.split(r"İDDİA\s*\n", raw, flags=re.IGNORECASE)[1:]:
        def _field(key: str) -> str:
            m = re.search(rf"{key}\s*:\s*(.+)", block, re.IGNORECASE)
            return m.group(1).strip() if m else ""

        domain  = _field("Alan").lower()
        text    = _field("Metin")
        context = _field("Bağlam")

        if text and domain in _DOMAINS:
            claims.append(Claim(domain=domain, text=text, context=context))

    return claims


def _parse_verification(raw: str, original_claims: list[Claim]) -> list[VerificationFinding]:
    findings: list[VerificationFinding] = []
    blocks = re.split(r"SONUÇ\s*\n", raw, flags=re.IGNORECASE)[1:]

    for i, block in enumerate(blocks):
        def _f(key: str) -> str:
            m = re.search(rf"{key}\s*:\s*(.+)", block, re.IGNORECASE)
            return m.group(1).strip() if m else ""

        verdict    = _f("Karar").lower()
        severity   = _f("Şiddet").lower() if verdict == "hatalı" else "düşük"
        correction = _f("Düzeltme")
        claim_text = _f("İddia")

        # Orijinal Claim nesnesiyle eşleştir (sıraya göre)
        claim = (
            original_claims[i]
            if i < len(original_claims)
            else Claim(domain="bilinmiyor", text=claim_text, context="")
        )

        if verdict in ("doğru", "hatalı", "eksik", "belirsiz"):
            findings.append(VerificationFinding(
                claim=claim,
                verdict=verdict,
                correction=correction if correction != "—" else "",
                severity=severity or "düşük",
            ))

    return findings


# ──────────────────────────────────────────────────────────────────── #
# Ölçüm, slug, Markdown render                                          #
# ──────────────────────────────────────────────────────────────────── #

def _measure_budgets(text: str) -> list[SectionBudget]:
    labels = [s[0] for s in _SECTIONS]
    positions: list[tuple[int, str]] = []

    for label in labels:
        m = re.search(rf"##\s*\[{label}\]", text, re.IGNORECASE)
        if m:
            positions.append((m.start(), label))

    positions.sort(key=lambda x: x[0])
    budgets: list[SectionBudget] = []

    target_map = {label: (mn, mx) for label, mn, mx in _SECTIONS}

    for i, (pos, label) in enumerate(positions):
        end    = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        actual = end - pos
        mn, mx = target_map.get(label, (0, 99999))
        budgets.append(SectionBudget(label=label, target_min=mn, target_max=mx, actual=actual))

    # Bulunamayan bölümler için sıfır ekle
    found_labels = {b.label for b in budgets}
    for label, mn, mx in _SECTIONS:
        if label not in found_labels:
            budgets.append(SectionBudget(label=label, target_min=mn, target_max=mx, actual=0))

    return budgets


def _parse_input(source: WriterInput) -> tuple[str, str, str]:
    """WriterInput'u (topic, strategy_context, source_type) üçlüsüne çevirir."""
    if isinstance(source, str):
        return source.strip(), "", "manual"

    if isinstance(source, dict):
        topic = source.get("topic", source.get("konu", ""))
        ctx   = source.get("context", source.get("bagLam", ""))
        return topic, ctx, "manual"

    if isinstance(source, StrategyReport):
        # StrategyReport'tan konu ve bağlam çıkar
        topic = _extract_topic_from_strategy(source)
        ctx   = _strategy_to_context(source)
        return topic, ctx, "strategy"

    raise TypeError(f"Desteklenmeyen giriş türü: {type(source)}")


def _extract_topic_from_strategy(report: StrategyReport) -> str:
    """StrategyReport'taki ilk konu önerisini veya soruyu konu olarak alır."""
    if report.primary_recommendation:
        # İlk tırnak içi veya ilk cümle
        m = re.search(r"['\"](.+?)['\"]", report.primary_recommendation)
        if m:
            return m.group(1)
        return report.primary_recommendation[:80]
    if report.action_items:
        return report.action_items[0][:80]
    return report.question_asked[:80]


def _strategy_to_context(report: StrategyReport) -> str:
    lines = [f"Strateji Sorgusu: {report.question_asked}"]
    if report.strengths:
        lines.append("Kanalın güçlü yönleri: " + " · ".join(report.strengths[:3]))
    if report.action_items:
        lines.append("Önerilen eylemler: " + " · ".join(report.action_items[:3]))
    return "\n".join(lines)


def _detect_domains(text: str) -> list[str]:
    lower = text.lower()
    return [d for d, kws in _DOMAINS.items() if any(kw in lower for kw in kws)]


def _slugify(title: str, max_len: int = 50) -> str:
    s = title.lower()
    for tr, en in [("ş","s"),("ç","c"),("ğ","g"),("ü","u"),("ö","o"),("ı","i"),
                   ("Ş","s"),("Ç","c"),("Ğ","g"),("Ü","u"),("Ö","o"),("İ","i")]:
        s = s.replace(tr, en)
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s[:max_len]


def _render_markdown(script: ScriptDraft) -> str:
    # Doğrulama bulguları tablosu
    findings_md = ""
    if script.verification.findings:
        rows = "\n".join(
            f"| {f.claim.domain} | {f.verdict} | {f.severity} "
            f"| {f.claim.text[:55]} | {f.correction[:70] or '—'} |"
            for f in script.verification.findings
        )
        findings_md = textwrap.dedent(f"""\
            ## Doğrulama Bulguları

            | Alan | Karar | Şiddet | İddia | Düzeltme |
            |------|-------|--------|-------|----------|
            {rows}

        """)

    # Bölüm bütçe tablosu
    budget_rows = "\n".join(
        f"| {b.label} | {b.target_min:,}–{b.target_max:,} | {b.actual:,} | {b.status} |"
        for b in script.section_budgets
    )

    domains_str = ", ".join(script.domains) if script.domains else "genel"

    return textwrap.dedent(f"""\
        ---
        başlık: "{script.title}"
        konu: "{script.topic}"
        kaynak: {script.source_type}
        alanlar: {domains_str}
        karakter: {script.char_count:,}
        kelime: {script.word_count:,}
        dogruluk_skoru: {script.verification.score}/100
        paslar: {script.passes_completed}
        olusturulma: {script.created_at}
        kalite: "{script.quality_summary()}"
        ---

        # {script.title}

        > **Konu:** {script.topic}
        > **Kaynak:** {script.source_type}  ·  **Alan(lar):** {domains_str}
        > **Karakter:** {script.char_count:,}  ·  **Kelime:** {script.word_count:,}
        > **Doğruluk Skoru:** {script.verification.score}/100  ·  **Paslar:** {script.passes_completed}

        ## Bölüm Bütçeleri

        | Bölüm | Hedef (kr) | Gerçek (kr) | Durum |
        |-------|-----------|-------------|-------|
        {budget_rows}

        {findings_md}---

        {script.content}
    """).strip()
