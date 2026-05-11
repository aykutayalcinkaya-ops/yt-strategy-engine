#!/usr/bin/env python3
"""
yt-strategy-engine  —  YouTube verisi + Claude API ile içerik üretim zinciri
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Komutlar
────────
  pipeline   Tam zincir: Veri Çek → Analiz Et → Fikir Üret → Senaryo Yaz
  create     Alan-doğrulamalı 12.000+ karakter senaryo üret (ContentCreator)
  analyze    Kanal + video istatistiklerini analiz et (VideoProcessor + AIHandler)
  strategy   YouTube verisine dayalı içerik stratejisi üret
  trending   Bölgeye göre trend video analizi
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from config import config
from src import (
    AIHandler,
    ChannelMetrics,
    ContentCreator,
    DataProcessor,
    StrategyEngine,
    VideoMetrics,
    VideoProcessor,
    YouTubeClient,
)

console = Console()

LOGO = textwrap.dedent("""\
    [bold cyan]yt-strategy-engine[/bold cyan]
    [dim]YouTube × Claude API  |  Veri Çek → Analiz Et → Fikir Üret → Senaryo Yaz[/dim]
""")


# ──────────────────────────────────────────────────────────────────── #
# Yardımcı görsel bileşenler                                            #
# ──────────────────────────────────────────────────────────────────── #

def _step(n: int, label: str, subtitle: str = "") -> None:
    sub = f"  [dim]{subtitle}[/dim]" if subtitle else ""
    console.print(Rule(f"[bold white]Adım {n}[/bold white] [cyan]{label}[/cyan]{sub}"))


def _ok(msg: str) -> None:
    console.print(f"[bold green]✓[/bold green] {msg}")


def _warn(msg: str) -> None:
    console.print(f"[yellow]⚠[/yellow] {msg}")


def _err(msg: str) -> None:
    console.print(f"[bold red]✗[/bold red] {msg}")


def _kv_table(rows: list[tuple[str, str]], title: str = "") -> Table:
    t = Table(show_header=False, box=None, padding=(0, 2))
    t.add_column(style="dim")
    t.add_column(style="white")
    for k, v in rows:
        t.add_row(k, v)
    if title:
        t.title = title
    return t


# ──────────────────────────────────────────────────────────────────── #
# KOMUT: pipeline                                                        #
# ──────────────────────────────────────────────────────────────────── #

def cmd_pipeline(args: argparse.Namespace) -> None:
    """
    Tam üretim zinciri:
      1. Veri Çek     — YouTube'dan video + (varsa) kanal verileri
      2. Analiz Et    — VideoProcessor + isteğe bağlı AIHandler kanal denetimi
      3. Fikir Üret   — Konu önerileri + içerik tavsiyeleri
      4. Senaryo Yaz  — ContentCreator ile 12.000+ karakter doğrulanmış senaryo
    """
    console.print(Panel(LOGO, border_style="cyan", padding=(0, 2)))

    yt = YouTubeClient()
    processor_kwargs = dict(channel_id="", channel_title="")

    # ── Adım 1: Veri Çek ──────────────────────────────────────────── #
    _step(1, "Veri Çek", args.topic)

    with console.status("[green]YouTube araması yapılıyor…"):
        videos: list[VideoMetrics] = yt.search_videos(
            args.topic, max_results=40, order="viewCount"
        )
    _ok(f"{len(videos)} video çekildi (konu: {args.topic})")

    channel: ChannelMetrics | None = None
    channel_videos: list[VideoMetrics] = []

    if args.channel:
        with console.status("[green]Kanal verileri çekiliyor…"):
            channel = yt.get_channel_metrics(args.channel)
            if channel:
                channel_videos = yt.get_channel_videos(args.channel, max_results=30)
                processor_kwargs = dict(
                    channel_id=channel.channel_id,
                    channel_title=channel.title,
                )
                _ok(
                    f"Kanal: [bold]{channel.title}[/bold]  |  "
                    f"{channel.subscriber_count:,} abone  |  "
                    f"{len(channel_videos)} video yüklendi"
                )
            else:
                _warn(f"Kanal bulunamadı: {args.channel}")

    # Analiz için kaynak: kanal videoları varsa onlar, yoksa arama sonuçları
    analysis_videos = channel_videos if channel_videos else videos

    # ── Adım 2: Analiz Et ─────────────────────────────────────────── #
    _step(2, "Analiz Et", f"{len(analysis_videos)} video işleniyor")

    sub_count = channel.subscriber_count if channel else 0
    vp = VideoProcessor(
        subscriber_count=sub_count,
        **processor_kwargs,
    )
    with console.status("[green]İstatistiksel analiz hesaplanıyor…"):
        report = vp.run(analysis_videos)

    # Format karşılaştırma tablosu
    fc = report.format_comparison
    fmt_table = Table(title="Shorts vs Long-form", box=None, show_header=True)
    fmt_table.add_column("Metrik", style="dim")
    fmt_table.add_column("Shorts", justify="right")
    fmt_table.add_column("Long-form", justify="right")
    fmt_table.add_row("Video sayısı", str(fc.shorts_count), str(fc.longform_count))
    fmt_table.add_row("Ort. izlenme", f"{fc.shorts_avg_views:,.0f}", f"{fc.longform_avg_views:,.0f}")
    fmt_table.add_row("Ort. etkileşim", f"%{fc.shorts_avg_engagement:.2f}", f"%{fc.longform_avg_engagement:.2f}")
    fmt_table.add_row("Tahmini CTR", f"%{fc.shorts_avg_ctr*100:.1f}", f"%{fc.longform_avg_ctr*100:.1f}")
    console.print(fmt_table)
    console.print(
        f"\n[bold]Önerilen format:[/bold] [cyan]{fc.recommended_format.value.upper()}[/cyan]  "
        f"— {fc.recommendation_reason}"
    )

    # Kazanan örüntüler özeti
    wp = report.winning_patterns
    opt_lo, opt_hi = wp.optimal_duration_range
    console.print(
        f"\n[bold]Kazanan örüntüler:[/bold] "
        f"Optimal süre [cyan]{opt_lo//60}–{opt_hi//60} dk[/cyan]  |  "
        f"Sayı içeren başlık %{wp.uses_numbers_in_title*100:.0f}  |  "
        f"Duygusal tetikleyici %{wp.uses_emotional_trigger*100:.0f}"
    )

    # Kanal denetimi (AI)
    ai_audit_summary = ""
    if channel and channel_videos:
        with console.status("[green]AI kanal denetimi yapılıyor…"):
            ai = AIHandler()
            audit = ai.audit_channel(channel, channel_videos)
        ai_audit_summary = audit.raw_response
        _ok("AI kanal denetimi tamamlandı")
        if audit.recommended_actions:
            console.print("[bold]Öncelikli eylemler:[/bold]")
            for i, action in enumerate(audit.recommended_actions[:3], 1):
                console.print(f"  {i}. {action}")

    # Teknik düzeltme gerektiren videolar
    if report.technical_fix_candidates:
        fix_table = Table(title=f"Teknik Düzeltme Adayları ({len(report.technical_fix_candidates)} video)", box=None)
        fix_table.add_column("Öncelik", justify="center", width=8)
        fix_table.add_column("Video", max_width=55)
        fix_table.add_column("Sorun Sayısı", justify="center", width=12)
        for fix in report.technical_fix_candidates[:8]:
            color = {"1": "red", "2": "yellow", "3": "dim"}.get(str(fix.priority), "white")
            fix_table.add_row(
                f"[{color}]P{fix.priority}[/{color}]",
                fix.video.title[:54],
                str(len(fix.issues)),
            )
        console.print(fix_table)

    # ── Adım 3: Fikir Üret ────────────────────────────────────────── #
    _step(3, "Fikir Üret", "Konu önerileri + içerik tavsiyeleri")

    topic_ideas = report.topic_recommendations
    if channel and channel_videos:
        with console.status("[green]AI içerik önerileri oluşturuluyor…"):
            ai = AIHandler()
            content_rec = ai.recommend_content(
                channel, channel_videos[:15], target_audience=args.audience
            )
        # AI önerilerini algoritmik önerilerle birleştir (tekilleştir)
        combined = list(dict.fromkeys(
            content_rec.topic_ideas + topic_ideas
        ))
        topic_ideas = combined
        if content_rec.seo_keywords:
            console.print(
                "[bold]Önerilen SEO kelimeleri:[/bold] " +
                ", ".join(content_rec.seo_keywords[:8])
            )

    if topic_ideas:
        idea_table = Table(title="Video Konu Önerileri", box=None)
        idea_table.add_column("#", width=4, style="dim")
        idea_table.add_column("Konu Fikri")
        for i, idea in enumerate(topic_ideas[:8], 1):
            idea_table.add_row(str(i), idea)
        console.print(idea_table)
    else:
        _warn("Konu önerisi üretilemedi — YouTube verisi yetersiz.")

    if args.no_scenario:
        console.print(Rule("[dim]Senaryo adımı atlandı (--no-scenario)[/dim]"))
        _print_pipeline_summary(report, saved_path="—")
        return

    # ── Adım 4: Senaryo Yaz ───────────────────────────────────────── #
    scenario_title = args.title
    if not scenario_title and topic_ideas:
        # İlk öneriyi başlık olarak kullan, parantez içini temizle
        first = topic_ideas[0]
        scenario_title = first.split("—")[0].strip().strip("'\"")
    if not scenario_title:
        scenario_title = args.topic

    _step(4, "Senaryo Yaz", scenario_title)

    creator = ContentCreator()
    with console.status(
        "[green]Taslak üretiliyor… (bu adım 1-2 dakika sürebilir)[/green]"
    ):
        draft = creator.create(
            topic=args.topic,
            title=scenario_title,
            extra_instructions=args.instructions or "",
            auto_save=True,
        )

    status_color = "green" if draft.meets_length_requirement else "yellow"
    console.print(
        f"\n[{status_color}]"
        f"{'✓' if draft.meets_length_requirement else '⚠'} "
        f"{draft.char_count:,} karakter[/{status_color}]  |  "
        f"{draft.word_count:,} kelime  |  "
        f"{draft.passes_completed} pas tamamlandı"
    )
    if draft.domains:
        console.print(f"[bold]Doğrulanan alanlar:[/bold] {', '.join(draft.domains)}")
    console.print(f"[bold]Doğrulama:[/bold] {draft.validation.summary()}")
    console.print(draft.validation.summary())

    sm = draft.section_metrics
    sec_table = _kv_table(
        [(k, f"{v:,} kr") for k, v in sm.as_dict().items()],
        title="Bölüm Metrikleri",
    )
    console.print(sec_table)
    console.print(f"\n[bold]Kaydedildi →[/bold] [cyan]{draft.saved_path}[/cyan]")

    _print_pipeline_summary(report, saved_path=draft.saved_path)


def _print_pipeline_summary(report, saved_path: str) -> None:
    console.print(Rule("[bold cyan]Pipeline Özeti[/bold cyan]"))
    rows = [
        ("Analiz edilen video", str(report.total_videos_analyzed)),
        ("Üst performanslı", str(len(report.top_performers))),
        ("Düzeltme adayı", str(len(report.technical_fix_candidates))),
        ("Konu önerisi", str(len(report.topic_recommendations))),
        ("Önerilen format", report.format_comparison.recommended_format.value.upper()),
        ("Senaryo dosyası", saved_path),
    ]
    console.print(_kv_table(rows))
    console.print()


# ──────────────────────────────────────────────────────────────────── #
# KOMUT: create   (ContentCreator standalone)                           #
# ──────────────────────────────────────────────────────────────────── #

def cmd_create(args: argparse.Namespace) -> None:
    console.print(Panel(
        f"[bold cyan]Senaryo Üretimi[/bold cyan]\n"
        f"Konu: [yellow]{args.topic}[/yellow]\n"
        f"Başlık: [yellow]{args.title or args.topic}[/yellow]",
        title="yt-strategy-engine  ·  ContentCreator",
    ))

    creator = ContentCreator()

    if args.stream:
        console.print(Rule("[dim]Taslak akışı başlıyor…[/dim]"))
        full = ""
        for chunk in creator.stream_create(topic=args.topic, title=args.title or args.topic):
            console.print(chunk, end="")
            full += chunk
        console.print()
        _ok(f"Akış tamamlandı ({len(full):,} karakter) — doğrulama ve kayıt yapılıyor…")
        # Doğrulama + kayıt için tam pipeline'ı çalıştır
        with console.status("[green]Doğrulama ve kayıt…"):
            draft = creator.create(
                topic=args.topic,
                title=args.title or args.topic,
                extra_instructions=args.instructions or "",
                auto_save=True,
            )
    else:
        with console.status(
            "[green]3-pas pipeline çalışıyor (taslak → doğrulama → düzeltme)…"
        ):
            draft = creator.create(
                topic=args.topic,
                title=args.title or args.topic,
                extra_instructions=args.instructions or "",
                auto_save=True,
            )

    status_color = "green" if draft.meets_length_requirement else "yellow"
    console.print(
        f"\n[{status_color}]"
        f"{'✓' if draft.meets_length_requirement else '⚠'} "
        f"{draft.char_count:,} karakter[/{status_color}]  |  "
        f"{draft.word_count:,} kelime"
    )
    console.print(f"[bold]Kalite:[/bold]       {draft.quality_badge()}")
    console.print(f"[bold]Doğrulama:[/bold]    {draft.validation.summary()}")
    if draft.domains:
        console.print(f"[bold]Alanlar:[/bold]      {', '.join(draft.domains)}")
    console.print(f"[bold]Kaydedildi →[/bold]  [cyan]{draft.saved_path}[/cyan]")

    if args.print_content:
        console.print(Rule())
        console.print(draft.content)


# ──────────────────────────────────────────────────────────────────── #
# KOMUT: analyze   (VideoProcessor + AIHandler standalone)              #
# ──────────────────────────────────────────────────────────────────── #

def cmd_analyze(args: argparse.Namespace) -> None:
    console.print(Panel(
        f"[bold cyan]Kanal & Video Analizi[/bold cyan]\n"
        f"Kanal ID: [yellow]{args.channel}[/yellow]",
        title="yt-strategy-engine  ·  Analyze",
    ))

    yt = YouTubeClient()

    with console.status("[green]Kanal verisi çekiliyor…"):
        channel = yt.get_channel_metrics(args.channel)
    if not channel:
        _err(f"Kanal bulunamadı: {args.channel}")
        sys.exit(1)

    with console.status(f"[green]{channel.title} kanalının videoları çekiliyor…"):
        videos = yt.get_channel_videos(args.channel, max_results=args.max_videos)
    _ok(f"{len(videos)} video yüklendi")

    vp = VideoProcessor(
        subscriber_count=channel.subscriber_count,
        channel_id=channel.channel_id,
        channel_title=channel.title,
    )
    with console.status("[green]İstatistiksel analiz hesaplanıyor…"):
        report = vp.run(videos)

    # Format tablosu
    fc = report.format_comparison
    fmt_table = Table(title="Shorts vs Long-form")
    fmt_table.add_column("Metrik")
    fmt_table.add_column(f"Shorts ({fc.shorts_count})", justify="right")
    fmt_table.add_column(f"Long-form ({fc.longform_count})", justify="right")
    fmt_table.add_row("Ort. izlenme", f"{fc.shorts_avg_views:,.0f}", f"{fc.longform_avg_views:,.0f}")
    fmt_table.add_row("Ort. etkileşim", f"%{fc.shorts_avg_engagement:.2f}", f"%{fc.longform_avg_engagement:.2f}")
    fmt_table.add_row("Tahmini CTR", f"%{fc.shorts_avg_ctr*100:.1f}", f"%{fc.longform_avg_ctr*100:.1f}")
    console.print(fmt_table)
    console.print(f"\n[bold]Öneri:[/bold] {fc.recommendation_reason}\n")

    # Teknik düzeltme listesi
    if report.technical_fix_candidates:
        console.print(Rule("[bold]Teknik Düzeltme Adayları[/bold]"))
        for fix in report.technical_fix_candidates[:args.max_fixes]:
            color = {"1": "red", "2": "yellow", "3": "dim"}.get(str(fix.priority), "white")
            console.print(
                f"\n  [{color}]P{fix.priority}[/{color}] [bold]{fix.video.title[:60]}[/bold]"
            )
            for issue, suggestion in zip(fix.issues, fix.fix_suggestions):
                console.print(f"    [dim]Sorun:[/dim]  {issue}")
                console.print(f"    [green]Öneri:[/green]  {suggestion}")

    # Konu önerileri
    if report.topic_recommendations:
        console.print(Rule("[bold]Video Konu Önerileri[/bold]"))
        for i, t in enumerate(report.topic_recommendations, 1):
            console.print(f"  {i:2}. {t}")

    # İsteğe bağlı AI denetimi
    if args.ai_audit:
        console.print(Rule("[bold]AI Kanal Denetimi[/bold]"))
        with console.status("[green]AI denetimi yapılıyor…"):
            ai = AIHandler()
            audit = ai.audit_channel(channel, videos)
        console.print(audit.raw_response)

    # İsteğe bağlı strateji prompt bağlamı çıktısı
    if args.dump_context:
        ctx_path = Path(config.output_dir) / f"context_{channel.channel_id}.txt"
        ctx_path.parent.mkdir(parents=True, exist_ok=True)
        ctx_path.write_text(report.strategy_prompt_context, encoding="utf-8")
        _ok(f"Prompt bağlamı kaydedildi → {ctx_path}")


# ──────────────────────────────────────────────────────────────────── #
# KOMUT: strategy  (StrategyEngine standalone — eski davranış korundu)  #
# ──────────────────────────────────────────────────────────────────── #

def cmd_strategy(args: argparse.Namespace) -> None:
    console.print(Panel(
        f"[bold cyan]İçerik Stratejisi[/bold cyan]\nKonu: [yellow]{args.topic}[/yellow]",
        title="yt-strategy-engine  ·  Strategy",
    ))

    engine = StrategyEngine()
    with console.status("[green]YouTube verisi çekiliyor ve strateji üretiliyor…"):
        strategy = engine.generate_strategy(
            topic=args.topic,
            target_audience=args.audience,
            competitor_channel_ids=args.competitors.split(",") if args.competitors else [],
        )

    console.print(strategy.raw_analysis)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(strategy.raw_analysis, encoding="utf-8")
        _ok(f"Strateji kaydedildi → {args.output}")


# ──────────────────────────────────────────────────────────────────── #
# KOMUT: trending                                                        #
# ──────────────────────────────────────────────────────────────────── #

def cmd_trending(args: argparse.Namespace) -> None:
    console.print(Panel(
        "[bold cyan]Trend Video Analizi[/bold cyan]",
        title="yt-strategy-engine  ·  Trending",
    ))

    yt = YouTubeClient()
    dp = DataProcessor()

    with console.status("[green]Trend videolar çekiliyor…"):
        videos = yt.get_trending_videos(category_id=args.category)
        stats = dp.aggregate_video_stats(videos)

    t = Table(title=f"Trend Analizi — Kategori {args.category}")
    t.add_column("Metrik")
    t.add_column("Değer", justify="right")
    t.add_row("Video sayısı", str(stats["total_videos"]))
    t.add_row("Toplam izlenme", f"{stats['total_views']:,}")
    t.add_row("Ort. izlenme", f"{stats['avg_views']:,}")
    t.add_row("Medyan izlenme", f"{stats['median_views']:,}")
    t.add_row("Ort. etkileşim", f"%{stats['avg_engagement_rate']}")
    t.add_row("Ort. süre", f"{stats['avg_duration_minutes']} dk")
    console.print(t)

    console.print(Rule("[bold]En Çok İzlenen 5 Video[/bold]"))
    for i, v in enumerate(stats.get("top_videos", []), 1):
        console.print(f"  {i}. [bold]{v.title}[/bold]")
        console.print(f"     {v.view_count:,} izlenme  |  %{v.engagement_rate} etkileşim  |  {v.duration_minutes} dk")


# ──────────────────────────────────────────────────────────────────── #
# CLI tanımı                                                             #
# ──────────────────────────────────────────────────────────────────── #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python main.py",
        description="YouTube × Claude API  —  içerik üretim zinciri",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Örnekler:
              python main.py pipeline "yapay zeka" --channel UCxxxxxx
              python main.py create "kuantum fiziği" --title "Kuantum Nedir?"
              python main.py analyze --channel UCxxxxxx --ai-audit
              python main.py strategy "Python programlama" --audience "öğrenciler"
              python main.py trending --category 28
        """),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── pipeline ──────────────────────────────────────────────────── #
    p = sub.add_parser(
        "pipeline",
        help="Tam zincir: Veri Çek → Analiz Et → Fikir Üret → Senaryo Yaz",
    )
    p.add_argument("topic", help="Ana konu (örn: 'kuantum fiziği')")
    p.add_argument("--channel", default="", metavar="CHANNEL_ID",
                   help="YouTube kanal ID (opsiyonel; derin analiz için)")
    p.add_argument("--audience", default="genel", help="Hedef kitle tanımı")
    p.add_argument("--title", default="", help="Senaryo başlığını elle belirle")
    p.add_argument("--instructions", default="", help="Senaryo için ek yönergeler")
    p.add_argument("--no-scenario", action="store_true",
                   help="Senaryo yazma adımını atla")
    p.set_defaults(func=cmd_pipeline)

    # ── create ────────────────────────────────────────────────────── #
    p = sub.add_parser(
        "create",
        help="Alan-doğrulamalı 12.000+ karakter senaryo üret",
    )
    p.add_argument("topic", help="Senaryo konusu")
    p.add_argument("--title", default="", help="Video başlığı (varsayılan: konu)")
    p.add_argument("--instructions", default="", help="Ek yönergeler")
    p.add_argument("--stream", action="store_true",
                   help="Taslağı gerçek zamanlı akışla yazdır")
    p.add_argument("--print-content", action="store_true",
                   help="Senaryo metnini terminale yazdır")
    p.set_defaults(func=cmd_create)

    # ── analyze ───────────────────────────────────────────────────── #
    p = sub.add_parser(
        "analyze",
        help="Kanal ve video istatistiklerini analiz et",
    )
    p.add_argument("--channel", required=True, metavar="CHANNEL_ID",
                   help="YouTube kanal ID")
    p.add_argument("--max-videos", type=int, default=30,
                   help="Çekilecek maksimum video sayısı (varsayılan: 30)")
    p.add_argument("--max-fixes", type=int, default=5,
                   help="Gösterilecek maksimum düzeltme adayı (varsayılan: 5)")
    p.add_argument("--ai-audit", action="store_true",
                   help="AI destekli kanal denetimi de çalıştır")
    p.add_argument("--dump-context", action="store_true",
                   help="Strateji prompt bağlamını dosyaya kaydet")
    p.set_defaults(func=cmd_analyze)

    # ── strategy ──────────────────────────────────────────────────── #
    p = sub.add_parser(
        "strategy",
        help="YouTube verisine dayalı 90 günlük içerik stratejisi",
    )
    p.add_argument("topic", help="Strateji konusu")
    p.add_argument("--audience", default="genel", help="Hedef kitle")
    p.add_argument("--competitors", default="",
                   help="Virgülle ayrılmış rakip kanal ID'leri")
    p.add_argument("--output", default="", help="Strateji dosya yolu (.md önerilir)")
    p.set_defaults(func=cmd_strategy)

    # ── trending ──────────────────────────────────────────────────── #
    p = sub.add_parser("trending", help="Bölgeye göre trend video analizi")
    p.add_argument("--category", default="0",
                   help="YouTube kategori ID (0=tüm, 10=müzik, 20=oyun, 28=teknoloji)")
    p.set_defaults(func=cmd_trending)

    return parser


# ──────────────────────────────────────────────────────────────────── #
# Giriş noktası                                                         #
# ──────────────────────────────────────────────────────────────────── #

def main() -> None:
    if not config.validate_keys():
        console.print(Panel(
            "[bold red]API anahtarları eksik![/bold red]\n\n"
            "Proje kökündeki [cyan].env[/cyan] dosyasını oluşturup şu iki satırı ekleyin:\n\n"
            "  [yellow]ANTHROPIC_API_KEY[/yellow]=sk-ant-...\n"
            "  [yellow]YOUTUBE_API_KEY[/yellow]=AIza...\n\n"
            "Detaylı rehber için [bold]API_SETUP.md[/bold] dosyasına bakın.",
            title="[red]Kurulum Gerekli[/red]",
            border_style="red",
        ))
        sys.exit(1)

    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
