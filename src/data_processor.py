from __future__ import annotations

import re
from statistics import mean, median, stdev
from typing import Any

from .youtube_client import VideoMetrics, ChannelMetrics


def _parse_duration_seconds(iso_duration: str) -> int:
    """Converts ISO 8601 duration (PT4M13S) to total seconds."""
    pattern = r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?"
    match = re.match(pattern, iso_duration)
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


class DataProcessor:
    def aggregate_video_stats(self, videos: list[VideoMetrics]) -> dict[str, Any]:
        if not videos:
            return {}

        views = [v.view_count for v in videos]
        likes = [v.like_count for v in videos]
        comments = [v.comment_count for v in videos]
        durations = [_parse_duration_seconds(v.duration) for v in videos]
        engagement_rates = [
            (v.like_count + v.comment_count) / v.view_count
            if v.view_count > 0 else 0
            for v in videos
        ]

        return {
            "total_videos": len(videos),
            "total_views": sum(views),
            "avg_views": round(mean(views)),
            "median_views": round(median(views)),
            "max_views": max(views),
            "min_views": min(views),
            "avg_likes": round(mean(likes)),
            "avg_comments": round(mean(comments)),
            "avg_engagement_rate": round(mean(engagement_rates) * 100, 2),
            "avg_duration_seconds": round(mean(durations)),
            "avg_duration_minutes": round(mean(durations) / 60, 1),
            "top_videos": sorted(videos, key=lambda v: v.view_count, reverse=True)[:5],
        }

    def extract_top_tags(self, videos: list[VideoMetrics], top_n: int = 20) -> list[str]:
        tag_counts: dict[str, int] = {}
        for video in videos:
            for tag in video.tags:
                tag_lower = tag.lower().strip()
                tag_counts[tag_lower] = tag_counts.get(tag_lower, 0) + 1
        return sorted(tag_counts, key=tag_counts.get, reverse=True)[:top_n]

    def format_stats_for_prompt(self, stats: dict[str, Any], topic: str) -> str:
        top_titles = "\n".join(
            f"  - {v.title} ({v.view_count:,} izlenme)"
            for v in stats.get("top_videos", [])
        )
        return f"""\
## YouTube Veri Analizi: {topic}

- Analiz edilen video sayısı: {stats.get('total_videos', 0)}
- Toplam izlenme: {stats.get('total_views', 0):,}
- Ortalama izlenme: {stats.get('avg_views', 0):,}
- Medyan izlenme: {stats.get('median_views', 0):,}
- Ortalama etkileşim oranı: %{stats.get('avg_engagement_rate', 0)}
- Ortalama video süresi: {stats.get('avg_duration_minutes', 0)} dakika

### En Çok İzlenen 5 Video
{top_titles}
"""

    def format_channel_for_prompt(self, channel: ChannelMetrics) -> str:
        return f"""\
## Kanal Analizi: {channel.title}

- Abone sayısı: {channel.subscriber_count:,}
- Toplam video: {channel.video_count:,}
- Toplam görüntülenme: {channel.view_count:,}
- Açıklama: {channel.description[:300]}...
"""
