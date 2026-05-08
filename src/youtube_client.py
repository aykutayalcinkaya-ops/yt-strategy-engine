from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from tenacity import retry, stop_after_attempt, wait_exponential


def _parse_duration_seconds(iso_duration: str) -> int:
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso_duration)
    if not match:
        return 0
    h = int(match.group(1) or 0)
    m = int(match.group(2) or 0)
    s = int(match.group(3) or 0)
    return h * 3600 + m * 60 + s


@dataclass
class VideoMetrics:
    video_id: str
    title: str
    channel_id: str
    channel_title: str
    view_count: int
    like_count: int
    comment_count: int
    duration_seconds: int
    duration_iso: str
    published_at: str
    description: str
    tags: list[str] = field(default_factory=list)
    thumbnail_url: str = ""
    category_id: str = ""

    @property
    def engagement_rate(self) -> float:
        if self.view_count == 0:
            return 0.0
        return round((self.like_count + self.comment_count) / self.view_count * 100, 4)

    @property
    def duration_minutes(self) -> float:
        return round(self.duration_seconds / 60, 1)


@dataclass
class ChannelMetrics:
    channel_id: str
    title: str
    handle: str
    subscriber_count: int
    video_count: int
    total_view_count: int
    description: str
    country: str
    created_at: str
    thumbnail_url: str = ""
    uploads_playlist_id: str = ""
    keywords: list[str] = field(default_factory=list)


@dataclass
class CommentData:
    comment_id: str
    video_id: str
    author: str
    text: str
    like_count: int
    published_at: str
    reply_count: int = 0
    is_top_level: bool = True


class YouTubeClient:
    """
    Google YouTube Data API v3 istemcisi.
    API anahtarı YOUTUBE_API_KEY ortam değişkeninden okunur.
    """

    def __init__(self):
        api_key = os.getenv("YOUTUBE_API_KEY")
        if not api_key:
            raise EnvironmentError("YOUTUBE_API_KEY ortam değişkeni tanımlı değil.")
        self._service = build("youtube", "v3", developerKey=api_key)
        self._region = os.getenv("YOUTUBE_REGION_CODE", "TR")
        self._language = os.getenv("YOUTUBE_LANGUAGE", "tr")
        self._max_results = int(os.getenv("YOUTUBE_MAX_RESULTS", "50"))

    # ------------------------------------------------------------------ #
    # Kanal                                                                 #
    # ------------------------------------------------------------------ #

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    def get_channel_metrics(self, channel_id: str) -> Optional[ChannelMetrics]:
        """Kanal istatistiklerini, meta verilerini ve uploads playlist ID'sini döndürür."""
        try:
            resp = (
                self._service.channels()
                .list(part="snippet,statistics,brandingSettings,contentDetails", id=channel_id)
                .execute()
            )
        except HttpError as e:
            raise RuntimeError(f"Kanal metrikleri alınamadı ({channel_id}): {e}") from e

        items = resp.get("items", [])
        if not items:
            return None

        item = items[0]
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        branding = item.get("brandingSettings", {}).get("channel", {})
        content_details = item.get("contentDetails", {})
        thumbnails = snippet.get("thumbnails", {})
        thumb = (thumbnails.get("high") or thumbnails.get("default") or {}).get("url", "")

        raw_keywords = branding.get("keywords", "")
        keywords = [k.strip().strip('"') for k in raw_keywords.split() if k.strip()]

        uploads_playlist = (
            content_details.get("relatedPlaylists", {}).get("uploads", "")
        )

        return ChannelMetrics(
            channel_id=channel_id,
            title=snippet.get("title", ""),
            handle=snippet.get("customUrl", ""),
            subscriber_count=int(stats.get("subscriberCount", 0)),
            video_count=int(stats.get("videoCount", 0)),
            total_view_count=int(stats.get("viewCount", 0)),
            description=snippet.get("description", ""),
            country=snippet.get("country", ""),
            created_at=snippet.get("publishedAt", ""),
            thumbnail_url=thumb,
            uploads_playlist_id=uploads_playlist,
            keywords=keywords,
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    def get_channel_videos(
        self,
        channel_id: str,
        max_results: int = 25,
        order: str = "date",
    ) -> list[VideoMetrics]:
        """
        Kanalın videolarını listeler.
        order: 'date' | 'viewCount' | 'rating'
        """
        channel = self.get_channel_metrics(channel_id)
        if not channel or not channel.uploads_playlist_id:
            # Uploads playlist yoksa search API'ye düş
            return self._search_channel_videos(channel_id, max_results)

        try:
            playlist_resp = (
                self._service.playlistItems()
                .list(
                    part="snippet,contentDetails",
                    playlistId=channel.uploads_playlist_id,
                    maxResults=min(max_results, 50),
                )
                .execute()
            )
        except HttpError as e:
            raise RuntimeError(f"Kanal videoları alınamadı ({channel_id}): {e}") from e

        video_ids = [
            item["contentDetails"]["videoId"]
            for item in playlist_resp.get("items", [])
        ]
        videos = self.get_video_details(video_ids)

        if order == "viewCount":
            videos.sort(key=lambda v: v.view_count, reverse=True)
        elif order == "rating":
            videos.sort(key=lambda v: v.engagement_rate, reverse=True)

        return videos

    def _search_channel_videos(self, channel_id: str, max_results: int) -> list[VideoMetrics]:
        try:
            resp = (
                self._service.search()
                .list(
                    part="id",
                    channelId=channel_id,
                    type="video",
                    order="date",
                    maxResults=min(max_results, 50),
                )
                .execute()
            )
        except HttpError as e:
            raise RuntimeError(f"Kanal video araması başarısız ({channel_id}): {e}") from e

        video_ids = [item["id"]["videoId"] for item in resp.get("items", [])]
        return self.get_video_details(video_ids)

    # ------------------------------------------------------------------ #
    # Video                                                                 #
    # ------------------------------------------------------------------ #

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    def get_video_details(self, video_ids: list[str]) -> list[VideoMetrics]:
        """Birden fazla video için snippet, statistics ve contentDetails döndürür."""
        if not video_ids:
            return []
        try:
            resp = (
                self._service.videos()
                .list(
                    part="snippet,statistics,contentDetails",
                    id=",".join(video_ids[:50]),
                )
                .execute()
            )
        except HttpError as e:
            raise RuntimeError(f"Video detayları alınamadı: {e}") from e

        return [self._parse_video(item) for item in resp.get("items", [])]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    def search_videos(
        self,
        query: str,
        max_results: int = None,
        order: str = "relevance",
        published_after: str = "",
    ) -> list[VideoMetrics]:
        """
        Anahtar kelimeye göre video arar ve VideoMetrics listesi döndürür.
        published_after: RFC 3339 formatı — '2024-01-01T00:00:00Z'
        """
        max_results = max_results or self._max_results
        kwargs: dict = dict(
            q=query,
            part="id,snippet",
            maxResults=min(max_results, 50),
            order=order,
            type="video",
            regionCode=self._region,
            relevanceLanguage=self._language,
        )
        if published_after:
            kwargs["publishedAfter"] = published_after
        try:
            resp = self._service.search().list(**kwargs).execute()
        except HttpError as e:
            raise RuntimeError(f"Video araması başarısız '{query}': {e}") from e

        video_ids = [item["id"]["videoId"] for item in resp.get("items", [])]
        return self.get_video_details(video_ids)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    def get_trending_videos(self, category_id: str = "0") -> list[VideoMetrics]:
        """Bölgeye göre trend videoları döndürür."""
        try:
            resp = (
                self._service.videos()
                .list(
                    part="snippet,statistics,contentDetails",
                    chart="mostPopular",
                    regionCode=self._region,
                    videoCategoryId=category_id,
                    maxResults=self._max_results,
                )
                .execute()
            )
        except HttpError as e:
            raise RuntimeError(f"Trend videolar alınamadı: {e}") from e

        return [self._parse_video(item) for item in resp.get("items", [])]

    # ------------------------------------------------------------------ #
    # Yorumlar                                                              #
    # ------------------------------------------------------------------ #

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    def get_video_comments(
        self,
        video_id: str,
        max_results: int = 100,
        order: str = "relevance",
        include_replies: bool = False,
    ) -> list[CommentData]:
        """
        Bir videonun üst düzey yorumlarını çeker.
        order: 'relevance' | 'time'
        include_replies: Her yorumun yanıtlarını da getir (ek API quota kullanımı).
        """
        comments: list[CommentData] = []
        page_token: Optional[str] = None
        fetched = 0

        while fetched < max_results:
            batch = min(max_results - fetched, 100)
            kwargs: dict = dict(
                part="snippet",
                videoId=video_id,
                maxResults=batch,
                order=order,
                textFormat="plainText",
            )
            if page_token:
                kwargs["pageToken"] = page_token

            try:
                resp = self._service.commentThreads().list(**kwargs).execute()
            except HttpError as e:
                if "commentsDisabled" in str(e):
                    break
                raise RuntimeError(f"Yorumlar alınamadı ({video_id}): {e}") from e

            for thread in resp.get("items", []):
                top = thread["snippet"]["topLevelComment"]["snippet"]
                comment = CommentData(
                    comment_id=thread["snippet"]["topLevelComment"]["id"],
                    video_id=video_id,
                    author=top.get("authorDisplayName", ""),
                    text=top.get("textDisplay", ""),
                    like_count=int(top.get("likeCount", 0)),
                    published_at=top.get("publishedAt", ""),
                    reply_count=int(thread["snippet"].get("totalReplyCount", 0)),
                    is_top_level=True,
                )
                comments.append(comment)

                if include_replies and comment.reply_count > 0:
                    replies = self._get_comment_replies(thread["id"])
                    comments.extend(replies)

            fetched += len(resp.get("items", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        return comments

    def _get_comment_replies(self, parent_id: str) -> list[CommentData]:
        try:
            resp = (
                self._service.comments()
                .list(part="snippet", parentId=parent_id, textFormat="plainText", maxResults=20)
                .execute()
            )
        except HttpError:
            return []

        replies = []
        for item in resp.get("items", []):
            s = item["snippet"]
            replies.append(CommentData(
                comment_id=item["id"],
                video_id=s.get("videoId", ""),
                author=s.get("authorDisplayName", ""),
                text=s.get("textDisplay", ""),
                like_count=int(s.get("likeCount", 0)),
                published_at=s.get("publishedAt", ""),
                reply_count=0,
                is_top_level=False,
            ))
        return replies

    # ------------------------------------------------------------------ #
    # Yardımcı                                                              #
    # ------------------------------------------------------------------ #

    def _parse_video(self, item: dict) -> VideoMetrics:
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        content = item.get("contentDetails", {})
        thumbnails = snippet.get("thumbnails", {})
        thumb = (
            thumbnails.get("maxres")
            or thumbnails.get("high")
            or thumbnails.get("default")
            or {}
        ).get("url", "")
        duration_iso = content.get("duration", "")
        return VideoMetrics(
            video_id=item["id"],
            title=snippet.get("title", ""),
            channel_id=snippet.get("channelId", ""),
            channel_title=snippet.get("channelTitle", ""),
            view_count=int(stats.get("viewCount", 0)),
            like_count=int(stats.get("likeCount", 0)),
            comment_count=int(stats.get("commentCount", 0)),
            duration_seconds=_parse_duration_seconds(duration_iso),
            duration_iso=duration_iso,
            published_at=snippet.get("publishedAt", ""),
            description=snippet.get("description", ""),
            tags=snippet.get("tags", []),
            thumbnail_url=thumb,
            category_id=snippet.get("categoryId", ""),
        )
