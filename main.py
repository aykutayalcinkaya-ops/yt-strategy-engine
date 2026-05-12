#!/usr/bin/env python3
"""
yt-manual-analyzer
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Argümansız çalıştırıldığında interaktif mod başlar:
  1. Analizler/ klasöründe yeni/değişmiş dosya var mı kontrol eder
  2. Bulursa analiz raporunu ekrana yazdırır
  3. "Bu verilere göre yeni bir video senaryosu hazırlamamı ister misin?"
  4. Onay gelirse senaryoyu oluşturur ve outputs/scripts/ altına kaydeder

Subcommand'lar (python main.py <komut> --help):
  scan      Analizler/ klasörünü tara, raporu göster (senaryo sormaz)
  create    WriterEngine ile senaryo üret
  strategy  YouTube API tabanlı içerik stratejisi
  trending  Trend video analizi
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.rule import Rule
from rich.table import Table

from config import config
from src import (
    AIStrategist,
    DataProcessor,
    FileProcessor,
    QueryType,
    StrategyEngine,
    WriterEngine,
    YouTubeClient,
)

console = Console()

_ANALIZLER_DIR  = Path("Analizler")
_STATE_FILE     = Path(".analizler_state.json")   # işlenen dosyaları izler
_VALID_EXTS     = {".csv", ".xlsx", ".xls"}

LOGO = textwrap.dedent("""\
    [bold cyan]yt-manual-analyzer[/bold cyan]
    [dim]Manuel Veri Analizi  ×  Claude API  |  Dosya Tara → Analiz → Senaryo Yaz[/dim]
""")


# ──────────────────────────────────────────────────────────────────── #
# Durum dosyası (hangi dosyalar zaten işlendi)                          #
# ──────────────────────────────────────────────────────────────────── #

def _load_state() -> dict[str, str]:
    """Daha önce işlenen dosyaların {yol: mtime} kaydını döndürür."""
    if _STATE_FILE.exists():
        try:
            return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_state(state: dict[str, str]) -> None:
    _STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _find_new_files(folder: Path, state: dict[str, str]) -> list[Path]:
    """Klasördeki dosyaları state ile karşılaştırır; yeni veya değişmiş olanları döndürür."""
    if not folder.exists():
        return []
    files = sorted(
        f for f in folder.iterdir()
        if f.suffix.lower() in _VALID_EXTS and not f.name.startswith("ORNEK")
    )
    return [f for f in files if state.get(str(f)) != str(f.stat().st_mtime)]


def _mark_processed(files: list[Path], state: dict[str, str]) -> None:
    for f in files:
        state[str(f)] = str(f.stat().st_mtime)
    _save_state(state)


# ──────────────────────────────────────────────────────────────────── #
# Görsel yardımcılar                                                    #
# ──────────────────────────────────────────────────────────────────── #

def _ok(msg: str)   -> None: console.print(f"[bold green]✓[/bold green] {msg}")
def _warn(msg: str) -> None: console.print(f"[yellow]⚠[/yellow]  {msg}")
def _err(msg: str)  -> None: console.print(f"[bold red]✗[/bold red] {msg}")
def _rule(title: str = "") -> None: console.print(Rule(f"[bold]{title}[/bold]" if title else ""))


def _print_stats_table(stats) -> None:
    """AggregatedStats'ı Rich tablosu olarak yazdırır."""
    t = Table(title="Genel İstatistikler", box=None, show_header=False, padding=(0, 3))
    t.add_column(style="dim", no_wrap=True)
    t.add_column(justify="right")
    rows = [
        ("Analiz edilen video",    str(stats.total_videos)),
        ("Toplam izlenme",         f"{stats.total_views:,}"),
        ("Toplam izleme süresi",   f"{stats.total_watch_time_hours:,.1f} saat"),
        ("Ortalama izlenme",       f"{stats.avg_views:,.0f}"),
        ("Medyan izlenme",         f"{stats.median_views:,.0f}"),
        ("Ortalama CTR",           f"%{stats.avg_ctr_pct}"),
        ("Medyan CTR",             f"%{stats.median_ctr_pct}"),
        ("Ort. izleme süresi",     stats.avg_watch_fmt),
        ("Toplam abone değişimi",  f"{stats.total_subscribers_gained:+,}"),
    ]
    for k, v in rows:
        t.add_row(k, v)
    console.print(t)

    # CTR dağılımı
    ctr_t = Table(title="CTR Dağılımı", box=None, padding=(0, 3))
    for bucket in stats.ctr_distribution:
        ctr_t.add_column(bucket, justify="center")
    ctr_t.add_row(*[str(v) for v in stats.ctr_distribution.values()])
    console.print(ctr_t)


def _print_top_bottom(stats) -> None:
    """En iyi ve en kötü videoları yazdırır."""
    if stats.top_performers:
        _rule("En Çok İzlenen Videolar")
        t = Table(box=None)
        t.add_column("#",    width=3,  style="dim")
        t.add_column("Başlık",         max_width=52)
        t.add_column("İzlenme",        justify="right")
        t.add_column("CTR %",          justify="right")
        t.add_column("Ort. İzleme",    justify="right")
        for i, v in enumerate(stats.top_performers, 1):
            t.add_row(str(i), v.title, f"{v.views:,}", f"%{v.ctr_pct}", v.avg_watch_fmt)
        console.print(t)

    if stats.underperformers:
        _rule("Gelişim Fırsatları (En Az İzlenen)")
        t = Table(box=None)
        t.add_column("#",    width=3,  style="dim")
        t.add_column("Başlık",         max_width=52)
        t.add_column("İzlenme",        justify="right")
        t.add_column("CTR %",          justify="right")
        t.add_column("Ort. İzleme",    justify="right")
        for i, v in enumerate(stats.underperformers, 1):
            color = "yellow" if v.ctr_pct < 2 else "white"
            t.add_row(str(i), f"[{color}]{v.title}[/{color}]",
                      f"{v.views:,}", f"%{v.ctr_pct}", v.avg_watch_fmt)
        console.print(t)


def _print_strategy_insights(strategy_report) -> None:
    """StrategyReport'tan temel içgörüleri ekrana basar."""
    _rule("AI Strateji İçgörüleri")

    if strategy_report.strengths:
        console.print("[bold green]Güçlü Yönler[/bold green]")
        for s in strategy_report.strengths[:4]:
            console.print(f"  [green]•[/green] {s}")

    if strategy_report.primary_recommendation:
        console.print(f"\n[bold]Birincil Öneri:[/bold]\n  {strategy_report.primary_recommendation}")

    if strategy_report.action_items:
        console.print("\n[bold]Bu Hafta Yapılacaklar:[/bold]")
        for i, item in enumerate(strategy_report.action_items[:4], 1):
            console.print(f"  [cyan]{i}.[/cyan] {item}")


# ──────────────────────────────────────────────────────────────────── #
# Analiz raporu — yeni dosyalar bulunduğunda çağrılır                  #
# ──────────────────────────────────────────────────────────────────── #

def run_analysis(new_files: list[Path]) -> tuple | None:
    """
    Yeni dosyaları okuyup analiz raporunu ekrana basar.
    (merged_report, strategy_report) döndürür; hata olursa None.
    """
    fp = FileProcessor()

    console.print()
    _ok(f"[bold]{len(new_files)}[/bold] yeni dosya bulundu:")
    for f in new_files:
        console.print(f"   [cyan]→[/cyan] {f.name}")
    console.print()

    with console.status("[green]Dosyalar okunuyor ve işleniyor…"):
        reports = [fp.load_file(f) for f in new_files]
        merged  = fp.merge(reports)

    if merged.warnings:
        for w in merged.warnings:
            _warn(w)

    if merged.stats is None:
        _err("Geçerli veri bulunamadı — dosyalarda zorunlu sütunlar eksik olabilir.")
        console.print(
            "  Beklenen sütunlar: [cyan]Video başlığı, İzlenme sayısı, "
            "Tıklama oranı (TO), Ortalama izleme süresi[/cyan]"
        )
        return None

    # ── İstatistik raporu ────────────────────────────────────────── #
    console.print(Panel(
        f"[bold]{merged.total_rows}[/bold] video  ·  "
        f"[bold]{len(merged.files)}[/bold] dosya birleştirildi",
        title="[cyan]Analiz Raporu[/cyan]",
        border_style="cyan",
    ))
    _print_stats_table(merged.stats)
    _print_top_bottom(merged.stats)

    # ── AI strateji içgörüleri ───────────────────────────────────── #
    console.print()
    with console.status("[green]AI strateji analizi yapılıyor…"):
        strategist     = AIStrategist(period_label="Son dönem")
        strategy_report = strategist.analyze(merged, query=QueryType.CONTENT_FOCUS)

    _print_strategy_insights(strategy_report)

    return merged, strategy_report


# ──────────────────────────────────────────────────────────────────── #
# Senaryo oluşturma adımı                                               #
# ──────────────────────────────────────────────────────────────────── #

def run_scenario_creation(strategy_report) -> None:
    """Kullanıcıdan başlık alarak WriterEngine ile senaryo üretir."""
    _rule()

    # Önerilen başlık
    suggested = ""
    if strategy_report.primary_recommendation:
        import re
        m = re.search(r"['\"](.+?)['\"]", strategy_report.primary_recommendation)
        suggested = m.group(1) if m else strategy_report.primary_recommendation[:70]

    console.print(
        "\n[bold]Senaryo başlığı[/bold] "
        f"[dim](Enter = önerilen başlığı kullan)[/dim]"
    )
    if suggested:
        console.print(f"  Öneri: [yellow]{suggested}[/yellow]")

    title = Prompt.ask("  Başlık", default=suggested).strip() or suggested or "Yeni Video"

    console.print()
    with console.status(
        "[green]Senaryo üretiliyor… "
        "(taslak → iddia çıkarımı → doğrulama → düzeltme — 2-3 dakika)[/green]"
    ):
        engine = WriterEngine()
        script = engine.write(strategy_report, title=title, auto_save=True)

    # ── Sonuç özeti ─────────────────────────────────────────────── #
    _rule("Senaryo Tamamlandı")
    length_ok  = script.meets_length
    verify_ok  = script.verification.is_clean
    len_color  = "green"  if length_ok  else "yellow"
    ver_color  = "green"  if verify_ok  else "yellow"

    console.print(
        f"[{len_color}]{'✓' if length_ok else '⚠'} "
        f"{script.char_count:,} karakter[/{len_color}]  ·  "
        f"{script.word_count:,} kelime  ·  "
        f"{script.passes_completed} pas"
    )
    console.print(
        f"[{ver_color}]{'✓' if verify_ok else '⚠'} "
        f"Doğruluk skoru: {script.verification.score}/100[/{ver_color}]  ·  "
        f"{script.verification.claims_extracted} iddia incelendi"
    )

    if script.domains:
        console.print(f"[bold]Alan(lar):[/bold] {', '.join(script.domains)}")

    # Bölüm bütçe tablosu
    budget_t = Table(title="Bölüm Metrikleri", box=None)
    budget_t.add_column("Bölüm",   style="dim")
    budget_t.add_column("Hedef",   justify="right")
    budget_t.add_column("Gerçek",  justify="right")
    budget_t.add_column("Durum",   justify="center")
    status_color = {"hedefte": "green", "kısa": "yellow", "uzun": "yellow", "eksik": "red"}
    for b in script.section_budgets:
        col = status_color.get(b.status, "white")
        budget_t.add_row(
            b.label,
            f"{b.target_min:,}–{b.target_max:,}",
            f"{b.actual:,}",
            f"[{col}]{b.status}[/{col}]",
        )
    console.print(budget_t)

    console.print(f"\n[bold]Kaydedildi →[/bold] [cyan]{script.saved_path}[/cyan]\n")


# ──────────────────────────────────────────────────────────────────── #
# İnteraktif mod  (argümansız çalıştırma)                               #
# ──────────────────────────────────────────────────────────────────── #

def run_interactive() -> None:
    console.print(Panel(LOGO, border_style="cyan", padding=(0, 2)))

    # Anthropic anahtarı zorunlu; YouTube opsiyonel
    if not config.anthropic_api_key:
        console.print(Panel(
            "[bold red]ANTHROPIC_API_KEY eksik![/bold red]\n\n"
            "Proje kökündeki [cyan].env[/cyan] dosyasına ekleyin:\n"
            "  [yellow]ANTHROPIC_API_KEY[/yellow]=sk-ant-...",
            title="[red]Kurulum Gerekli[/red]",
            border_style="red",
        ))
        sys.exit(1)

    # ── 1. Yeni dosya kontrolü ───────────────────────────────────── #
    _rule("Analizler/ Klasörü Taranıyor")

    if not _ANALIZLER_DIR.exists():
        _warn(
            f"'{_ANALIZLER_DIR}' klasörü bulunamadı. "
            "Klasörü oluşturup YouTube Studio CSV dosyalarını içine kopyalayın."
        )
        sys.exit(0)

    state    = _load_state()
    new_files = _find_new_files(_ANALIZLER_DIR, state)

    if not new_files:
        all_files = [
            f for f in _ANALIZLER_DIR.iterdir()
            if f.suffix.lower() in _VALID_EXTS and not f.name.startswith("ORNEK")
        ]
        if all_files:
            console.print(
                f"[dim]Analizler/ klasöründe {len(all_files)} dosya var "
                "ancak hepsi daha önce işlendi.[/dim]"
            )
            if Confirm.ask("Yine de analizi yeniden çalıştırmak ister misin?", default=False):
                # Durumu temizle, hepsini yeniden işle
                new_files = all_files
                state = {}
            else:
                console.print("[dim]Çıkılıyor. Yeni dosya eklenince tekrar çalıştırın.[/dim]")
                sys.exit(0)
        else:
            console.print(
                "[yellow]Analizler/ klasöründe CSV veya Excel dosyası bulunamadı.[/yellow]\n"
                "YouTube Studio → Analitik → Gelişmiş Mod → İndir (CSV) adımlarını izleyin."
            )
            sys.exit(0)

    # ── 2. Analiz raporunu ekrana yazdır ────────────────────────── #
    result = run_analysis(new_files)
    if result is None:
        sys.exit(1)

    merged, strategy_report = result

    # ── 3. Senaryo sorusu ────────────────────────────────────────── #
    console.print()
    if Confirm.ask(
        "[bold]Bu verilere göre yeni bir video senaryosu hazırlamamı ister misin?[/bold]",
        default=True,
    ):
        run_scenario_creation(strategy_report)
        # Başarılı işlem sonrası dosyaları "işlendi" olarak kaydet
        _mark_processed(new_files, state)
    else:
        console.print("[dim]Senaryo oluşturulmadı. Veriler işlendi olarak kaydedildi.[/dim]")
        _mark_processed(new_files, state)


# ──────────────────────────────────────────────────────────────────── #
# KOMUT: scan  (sadece tara, senaryo sorusu yok)                        #
# ──────────────────────────────────────────────────────────────────── #

def cmd_scan(args: argparse.Namespace) -> None:
    folder = Path(args.dir)
    if not folder.exists():
        _err(f"Klasör bulunamadı: {folder}")
        sys.exit(1)

    state     = {} if args.force else _load_state()
    new_files = _find_new_files(folder, state)

    if not new_files:
        _warn("Yeni veya değişmiş dosya bulunamadı.")
        if not args.force:
            console.print("[dim]--force ile tüm dosyaları yeniden işleyebilirsiniz.[/dim]")
        sys.exit(0)

    result = run_analysis(new_files)
    if result is None:
        sys.exit(1)

    _mark_processed(new_files, state)
    _ok("Dosyalar işlendi ve kaydedildi.")


# ──────────────────────────────────────────────────────────────────── #
# KOMUT: create  (WriterEngine standalone)                              #
# ──────────────────────────────────────────────────────────────────── #

def cmd_create(args: argparse.Namespace) -> None:
    console.print(Panel(
        f"[bold cyan]Senaryo Üretimi[/bold cyan]\n"
        f"Konu  : [yellow]{args.topic}[/yellow]\n"
        f"Başlık: [yellow]{args.title or args.topic}[/yellow]",
        title="yt-manual-analyzer  ·  WriterEngine",
    ))

    engine = WriterEngine()

    if args.stream:
        _rule("Taslak Akışı")
        for chunk in engine.stream_draft(args.topic, title=args.title or args.topic):
            console.print(chunk, end="")
        console.print()
        _ok(f"Akış tamamlandı — tam pipeline başlatılıyor…")

    with console.status("[green]4-pas pipeline: taslak → iddia → doğrula → düzelt…"):
        script = engine.write(
            source=args.topic,
            title=args.title or args.topic,
            extra_instructions=args.instructions or "",
            auto_save=True,
        )

    run_scenario_creation.__doc__  # reuse result display
    _rule("Senaryo Tamamlandı")
    len_color = "green" if script.meets_length else "yellow"
    ver_color = "green" if script.verification.is_clean else "yellow"
    console.print(
        f"[{len_color}]{'✓' if script.meets_length else '⚠'} "
        f"{script.char_count:,} karakter[/{len_color}]  ·  "
        f"{script.word_count:,} kelime"
    )
    console.print(
        f"[{ver_color}]Doğruluk: {script.verification.score}/100[/{ver_color}]  ·  "
        f"{script.quality_summary()}"
    )
    console.print(f"\n[bold]Kaydedildi →[/bold] [cyan]{script.saved_path}[/cyan]\n")

    if args.print_content:
        _rule()
        console.print(script.content)


# ──────────────────────────────────────────────────────────────────── #
# KOMUT: strategy  (StrategyEngine — YouTube API tabanlı)               #
# ──────────────────────────────────────────────────────────────────── #

def cmd_strategy(args: argparse.Namespace) -> None:
    if not config.youtube_api_key:
        _err("YOUTUBE_API_KEY gerekli. .env dosyasına ekleyin.")
        sys.exit(1)

    console.print(Panel(
        f"[bold cyan]İçerik Stratejisi[/bold cyan]\nKonu: [yellow]{args.topic}[/yellow]",
        title="yt-manual-analyzer  ·  Strategy",
    ))

    from src import StrategyEngine
    engine = StrategyEngine()
    with console.status("[green]YouTube verisi çekiliyor ve strateji üretiliyor…"):
        strategy = engine.generate_strategy(
            topic=args.topic,
            target_audience=args.audience,
            competitor_channel_ids=args.competitors.split(",") if args.competitors else [],
        )
    console.print(strategy.raw_analysis)

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(strategy.raw_analysis, encoding="utf-8")
        _ok(f"Strateji kaydedildi → {args.output}")


# ──────────────────────────────────────────────────────────────────── #
# KOMUT: trending                                                        #
# ──────────────────────────────────────────────────────────────────── #

def cmd_trending(args: argparse.Namespace) -> None:
    if not config.youtube_api_key:
        _err("YOUTUBE_API_KEY gerekli. .env dosyasına ekleyin.")
        sys.exit(1)

    console.print(Panel("[bold cyan]Trend Video Analizi[/bold cyan]",
                        title="yt-manual-analyzer  ·  Trending"))

    yt = YouTubeClient()
    dp = DataProcessor()

    with console.status("[green]Trend videolar çekiliyor…"):
        videos = yt.get_trending_videos(category_id=args.category)
        stats  = dp.aggregate_video_stats(videos)

    t = Table(title=f"Trend Analizi — Kategori {args.category}")
    t.add_column("Metrik")
    t.add_column("Değer", justify="right")
    for k, v in [
        ("Video sayısı",    str(stats["total_videos"])),
        ("Toplam izlenme",  f"{stats['total_views']:,}"),
        ("Ort. izlenme",    f"{stats['avg_views']:,}"),
        ("Medyan izlenme",  f"{stats['median_views']:,}"),
        ("Ort. etkileşim",  f"%{stats['avg_engagement_rate']}"),
        ("Ort. süre",       f"{stats['avg_duration_minutes']} dk"),
    ]:
        t.add_row(k, v)
    console.print(t)

    _rule("En Çok İzlenen 5 Video")
    for i, v in enumerate(stats.get("top_videos", []), 1):
        console.print(f"  {i}. [bold]{v.title}[/bold]")
        console.print(f"     {v.view_count:,} izlenme  ·  %{v.engagement_rate} etkileşim  ·  {v.duration_minutes} dk")


# ──────────────────────────────────────────────────────────────────── #
# CLI tanımı                                                             #
# ──────────────────────────────────────────────────────────────────── #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python main.py",
        description="yt-manual-analyzer — Manuel veri analizi ve teknik senaryo üretimi",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Örnekler:
              python main.py                    # interaktif mod (önerilen)
              python main.py scan               # sadece tara, senaryo sorma
              python main.py scan --force       # tüm dosyaları yeniden işle
              python main.py create "kuantum fiziği" --title "Kuantum Nedir?"
              python main.py strategy "Python" --audience "öğrenciler"
              python main.py trending --category 28
        """),
    )
    sub = parser.add_subparsers(dest="command")

    # ── scan ──────────────────────────────────────────────────────── #
    p = sub.add_parser("scan", help="Analizler/ klasörünü tara, raporu göster")
    p.add_argument("--dir", default="Analizler", help="Taranacak klasör (varsayılan: Analizler)")
    p.add_argument("--force", action="store_true", help="Daha önce işlenenleri de yeniden çalıştır")
    p.set_defaults(func=cmd_scan)

    # ── create ────────────────────────────────────────────────────── #
    p = sub.add_parser("create", help="WriterEngine ile doğrulamalı senaryo üret")
    p.add_argument("topic",  help="Senaryo konusu")
    p.add_argument("--title",         default="",    help="Video başlığı (varsayılan: konu)")
    p.add_argument("--instructions",  default="",    help="Ek yönergeler")
    p.add_argument("--stream",        action="store_true", help="Taslağı akışla yazdır")
    p.add_argument("--print-content", action="store_true", help="Senaryo metnini terminale yazdır")
    p.set_defaults(func=cmd_create)

    # ── strategy ──────────────────────────────────────────────────── #
    p = sub.add_parser("strategy", help="YouTube API tabanlı içerik stratejisi (YOUTUBE_API_KEY gerekli)")
    p.add_argument("topic")
    p.add_argument("--audience",    default="genel")
    p.add_argument("--competitors", default="")
    p.add_argument("--output",      default="")
    p.set_defaults(func=cmd_strategy)

    # ── trending ──────────────────────────────────────────────────── #
    p = sub.add_parser("trending", help="Trend video analizi (YOUTUBE_API_KEY gerekli)")
    p.add_argument("--category", default="0",
                   help="YouTube kategori ID (0=tüm, 10=müzik, 20=oyun, 28=teknoloji)")
    p.set_defaults(func=cmd_trending)

    # ── gui ───────────────────────────────────────────────────────── #
    p = sub.add_parser("gui", help="Grafiksel arayüzü başlat (customtkinter gerekli)")
    p.set_defaults(func=lambda _: _cmd_gui())

    return parser


def _cmd_gui() -> None:
    try:
        from src.gui_app import launch
        launch()
    except ImportError:
        _err("customtkinter bulunamadı. Yüklemek için:\n  pip install customtkinter")


# ──────────────────────────────────────────────────────────────────── #
# Giriş noktası                                                         #
# ──────────────────────────────────────────────────────────────────── #

def main() -> None:
    # PyInstaller EXE olarak çalışıyorsa doğrudan GUI başlat
    if getattr(sys, "frozen", False):
        _cmd_gui()
        return

    # Argümansız çalışırsa → interaktif mod
    if len(sys.argv) == 1:
        run_interactive()
        return

    parser = build_parser()
    args   = parser.parse_args()

    if args.command is None:
        run_interactive()
        return

    args.func(args)


if __name__ == "__main__":
    main()
