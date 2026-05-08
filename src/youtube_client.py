from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from tenacity import retry, stop_after_attempt, wait_exponential

from config import config


@dataclass
class VideoMetrics:
    video_id: str
    title: str
    channel_title: str
    view_count: int
    like_count: int
    comment_count: int
    duration: str
    published_at: str
    description: str
    tags: list[str] = field(default_factory=list)
    thumbnail_url: str = ""


@dataclass
class ChannelMetrics:
    channel_id: str
    title: str
    subscriber_count: int
    video_count: int
    view_count: int
    description: str
    top_videos: list[VideoMetrics] = field(default_factory=list)


class YouTubeClient:
    def __init__(self):
        self._service = build(
            "youtube", "v3", developerKey=config.youtube_api_key
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def search_videos(
        self,
        query: str,
        max_results: int = None,
        order: str = "relevance",
    ) -> list[VideoMetrics]:
        max_results = max_results or config.youtube_max_results
        try:
            search_response = (
                self._service.search()
                .list(
                    q=query,
                    part="id,snippet",
                    maxResults=max_results,
                    order=order,
                    type="video",
                    regionCode=config.youtube_region_code,
                    relevanceLanguage=config.youtube_language,
                )
                .execute()
            )
            video_ids = [
                item["id"]["videoId"]
                for item in search_response.get("items", [])
            ]
            return self.get_video_details(video_ids)
        except HttpError as e:
            raise RuntimeError(f"YouTube search failed: {e}") from e

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def get_video_details(self, video_ids: list[str]) -> list[VideoMetrics]:
        if not video_ids:
            return []
        try:
            response = (
                self._service.videos()
                .list(
                    part="snippet,statistics,contentDetails",
                    id=",".join(video_ids),
                )
                .execute()
            )
            return [self._parse_video(item) for item in response.get("items", [])]
        except HttpError as e:
            raise RuntimeError(f"YouTube video details fetch failed: {e}") from e

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def get_channel_metrics(self, channel_id: str) -> Optional[ChannelMetrics]:
        try:
            response = (
                self._service.channels()
                .list(part="snippet,statistics", id=channel_id)
                .execute()
            )
            items = response.get("items", [])
            if not items:
                return None
            item = items[0]
            stats = item.get("statistics", {})
            snippet = item.get("snippet", {})
            return ChannelMetrics(
                channel_id=channel_id,
                title=snippet.get("title", ""),
                subscriber_count=int(stats.get("subscriberCount", 0)),
                video_count=int(stats.get("videoCount", 0)),
                view_count=int(stats.get("viewCount", 0)),
                description=snippet.get("description", ""),
            )
        except HttpError as e:
            raise RuntimeError(f"Channel metrics fetch failed: {e}") from e

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def get_trending_videos(self, category_id: str = "0") -> list[VideoMetrics]:
        try:
            response = (
                self._service.videos()
                .list(
                    part="snippet,statistics,contentDetails",
                    chart="mostPopular",
                    regionCode=config.youtube_region_code,
                    videoCategoryId=category_id,
                    maxResults=config.youtube_max_results,
                )
                .execute()
            )
            return [self._parse_video(item) for item in response.get("items", [])]
        except HttpError as e:
            raise RuntimeError(f"Trending videos fetch failed: {e}") from e

    def _parse_video(self, item: dict) -> VideoMetrics:
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        content = item.get("contentDetails", {})
        thumbnails = snippet.get("thumbnails", {})
        thumbnail = (
            thumbnails.get("maxres", thumbnails.get("high", thumbnails.get("default", {})))
        ).get("url", "")
        return VideoMetrics(
            video_id=item["id"],
            title=snippet.get("title", ""),
            channel_title=snippet.get("channelTitle", ""),
            view_count=int(stats.get("viewCount", 0)),
            like_count=int(stats.get("likeCount", 0)),
            comment_count=int(stats.get("commentCount", 0)),
            duration=content.get("duration", ""),
            published_at=snippet.get("publishedAt", ""),
            description=snippet.get("description", ""),
            tags=snippet.get("tags", []),
            thumbnail_url=thumbnail,
        )
