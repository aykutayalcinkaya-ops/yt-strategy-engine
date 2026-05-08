#!/usr/bin/env python3
"""
yt-strategy-engine: YouTube veri analizi ile Claude API destekli içerik stratejisi
ve 12.000 karakterlik teknik senaryo üretici.
"""

import argparse
import sys

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from config import config
from src import StrategyEngine, ScenarioGenerator, DataProcessor, YouTubeClient

console = Console()


def cmd_strategy(args: argparse.Namespace) -> None:
    console.print(Panel(
        f"[bold cyan]Strateji Üretiliyor[/bold cyan]\nKonu: [yellow]{args.topic}[/yellow]",
        title="yt-strategy-engine"
    ))

    engine = StrategyEngine()
    with console.status("[bold green]YouTube verileri çekiliyor ve strateji analiz ediliyor..."):
        strategy = engine.generate_strategy(
            topic=args.topic,
            target_audience=args.audience,
            competitor_channel_ids=args.competitors.split(",") if args.competitors else [],
        )

    console.print("\n[bold green]İçerik Stratejisi Hazır![/bold green]\n")
    console.print(strategy.raw_analysis)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(strategy.raw_analysis)
        console.print(f"\n[dim]Strateji kaydedildi: {args.output}[/dim]")


def cmd_scenario(args: argparse.Namespace) -> None:
    console.print(Panel(
        f"[bold cyan]Senaryo Üretiliyor[/bold cyan]\nBaşlık: [yellow]{args.title}[/yellow]",
        title="yt-strategy-engine"
    ))

    generator = ScenarioGenerator()
    strategy = None

    if args.with_strategy:
        with console.status("[bold green]Strateji analizi yapılıyor..."):
            engine = StrategyEngine()
            strategy = engine.generate_strategy(topic=args.topic)

    with console.status("[bold green]12.000 karakterlik senaryo yazılıyor..."):
        scenario = generator.generate(
            topic=args.topic,
            title=args.title,
            strategy=strategy,
            custom_instructions=args.instructions or "",
        )

    status_icon = "✓" if scenario.meets_length_requirement else "✗"
    status_color = "green" if scenario.meets_length_requirement else "red"

    console.print(f"\n[{status_color}]{status_icon} Karakter sayısı: {scenario.char_count:,}[/{status_color}]")
    console.print(f"Kelime sayısı: {scenario.word_count:,}\n")

    saved_path = generator.save(scenario, config.output_dir)
    console.print(f"[dim]Senaryo kaydedildi: {saved_path}[/dim]")

    if args.print:
        console.print("\n" + "=" * 80)
        console.print(scenario.content)


def cmd_trending(args: argparse.Namespace) -> None:
    console.print(Panel("[bold cyan]Trend Videolar Analiz Ediliyor[/bold cyan]", title="yt-strategy-engine"))

    yt = YouTubeClient()
    processor = DataProcessor()

    with console.status("[bold green]Trend videolar çekiliyor..."):
        videos = yt.get_trending_videos(category_id=args.category)
        stats = processor.aggregate_video_stats(videos)

    console.print(f"\n[bold]Analiz Edilen Video:[/bold] {stats['total_videos']}")
    console.print(f"[bold]Toplam İzlenme:[/bold] {stats['total_views']:,}")
    console.print(f"[bold]Ort. İzlenme:[/bold] {stats['avg_views']:,}")
    console.print(f"[bold]Ort. Süre:[/bold] {stats['avg_duration_minutes']} dk")
    console.print(f"[bold]Ort. Etkileşim:[/bold] %{stats['avg_engagement_rate']}\n")

    console.print("[bold]En Çok İzlenen 5 Video:[/bold]")
    for i, v in enumerate(stats.get("top_videos", []), 1):
        console.print(f"  {i}. {v.title} — {v.view_count:,} izlenme")


def main() -> None:
    if not config.validate_keys():
        console.print("[bold red]Hata:[/bold red] ANTHROPIC_API_KEY ve YOUTUBE_API_KEY .env dosyasında tanımlı olmalıdır.")
        sys.exit(1)

    parser = argparse.ArgumentParser(
        prog="yt-strategy-engine",
        description="YouTube verisi + Claude API ile içerik stratejisi ve senaryo üretici",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # strategy subcommand
    p_strategy = subparsers.add_parser("strategy", help="YouTube verisine dayalı içerik stratejisi üret")
    p_strategy.add_argument("topic", help="Strateji konusu (örn: 'Python programlama')")
    p_strategy.add_argument("--audience", default="genel", help="Hedef kitle tanımı")
    p_strategy.add_argument("--competitors", default="", help="Virgülle ayrılmış rakip kanal ID'leri")
    p_strategy.add_argument("--output", default="", help="Stratejiyi kaydet (dosya yolu)")
    p_strategy.set_defaults(func=cmd_strategy)

    # scenario subcommand
    p_scenario = subparsers.add_parser("scenario", help="12.000 karakterlik teknik senaryo üret")
    p_scenario.add_argument("topic", help="Video konusu")
    p_scenario.add_argument("title", help="Video başlığı")
    p_scenario.add_argument("--instructions", default="", help="Ek talimatlar")
    p_scenario.add_argument("--with-strategy", action="store_true", help="Strateji analizi de yap")
    p_scenario.add_argument("--print", action="store_true", help="Senaryoyu terminale yazdır")
    p_scenario.set_defaults(func=cmd_scenario)

    # trending subcommand
    p_trending = subparsers.add_parser("trending", help="Trend video analizi yap")
    p_trending.add_argument("--category", default="0", help="YouTube kategori ID'si (varsayılan: 0 = tüm)")
    p_trending.set_defaults(func=cmd_trending)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
