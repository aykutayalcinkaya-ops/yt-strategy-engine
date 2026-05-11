"""
src/file_processor.py
~~~~~~~~~~~~~~~~~~~~~
Analizler/ klasöründeki CSV ve Excel dosyalarını okur, YouTube Studio
sütun isimlerini standartlaştırır, önemli metrikleri (izlenme, CTR,
ortalama izleme süresi) özetler ve Claude'a gönderilmeye hazır bir
prompt bağlamı üretir.

Desteklenen dosya formatları : .csv  |  .xlsx  |  .xls
Desteklenen dil varyantları  : YouTube Studio TR  |  YouTube Studio EN
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from textwrap import dedent
from typing import Any

import pandas as pd


# ──────────────────────────────────────────────────────────────────── #
# Sütun eşleme tablosu                                                  #
# YouTube Studio farklı locale ve dışa aktarım türlerinde              #
# farklı sütun adları kullanır. Tüm varyantlar standart ada çekilir.   #
# ──────────────────────────────────────────────────────────────────── #

_COLUMN_MAP: dict[str, list[str]] = {
    # Standart ad → [tanınacak varyantlar]
    "title": [
        "video başlığı", "içerik", "video title", "title", "content",
        "video adı", "başlık",
    ],
    "video_id": [
        "video kimliği", "video id", "content id", "içerik kimliği",
    ],
    "published_at": [
        "yayınlanma tarihi", "yayın tarihi", "publish date", "published at",
        "video publish time", "tarih",
    ],
    "views": [
        "izlenme sayısı", "izlenmeler", "görüntülenme", "views", "view count",
        "izlenme", "görüntülenme sayısı",
    ],
    "ctr": [
        "tıklama oranı (to)", "tıklama oranı", "to (%)", "to", "ctr (%)",
        "click-through rate (ctr)", "ctr", "impression ctr",
        "gösterim tıklama oranı",
    ],
    "avg_watch_duration": [
        "ortalama izleme süresi", "ortalama görüntülenme süresi",
        "average view duration", "avg view duration",
        "ortalama izlenme süresi",
    ],
    "watch_time_hours": [
        "izleme süresi (saat)", "izleme süresi", "watch time (hours)",
        "watch time hours", "toplam izleme süresi",
    ],
    "impressions": [
        "gösterim sayısı", "gösterimler", "impressions",
    ],
    "subscribers_gained": [
        "abone kazanımı", "kazanılan abone", "subscribers gained",
        "net subscribers", "abone değişimi",
    ],
    "likes": [
        "beğeni sayısı", "beğeniler", "likes",
    ],
    "comments": [
        "yorum sayısı", "yorumlar", "comments",
    ],
    "shares": [
        "paylaşım sayısı", "paylaşımlar", "shares",
    ],
}

# Kullanıcıya gösterilecek ve prompt'ta kullanılacak zorunlu metrikler
_KEY_METRICS = ["title", "views", "ctr", "avg_watch_duration"]
_OPTIONAL_METRICS = [
    "watch_time_hours", "impressions", "subscribers_gained",
    "likes", "comments", "shares", "published_at",
]


# ──────────────────────────────────────────────────────────────────── #
# Veri yapıları                                                         #
# ──────────────────────────────────────────────────────────────────── #

@dataclass
class ColumnReport:
    """Hangi sütunların bulunup bulunmadığını raporlar."""
    found: list[str]           # standart ad olarak
    missing_required: list[str]
    missing_optional: list[str]
    raw_to_standard: dict[str, str]  # orijinal ad → standart ad


@dataclass
class VideoSummaryRow:
    title: str
    views: int
    ctr_pct: float             # yüzde olarak (örn. 4.5)
    avg_watch_seconds: int
    avg_watch_fmt: str         # "3:45"
    watch_time_hours: float
    subscribers_gained: int
    engagement_score: float    # views × ctr / 100  (tahmini erişim)


@dataclass
class AggregatedStats:
    total_videos: int
    total_views: int
    total_watch_time_hours: float
    avg_views: float
    median_views: float
    avg_ctr_pct: float
    median_ctr_pct: float
    avg_watch_seconds: int
    avg_watch_fmt: str
    total_subscribers_gained: int
    top_performers: list[VideoSummaryRow]    # izlenmeye göre üst 5
    underperformers: list[VideoSummaryRow]   # izlenmeye göre alt 5
    ctr_distribution: dict[str, int]         # bucket → video sayısı


@dataclass
class FileReport:
    """Tek bir dosyadan üretilen rapor."""
    file_path: str
    file_name: str
    rows_loaded: int
    column_report: ColumnReport
    df: pd.DataFrame                  # normalize edilmiş DataFrame
    stats: AggregatedStats | None
    prompt_context: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class MergedReport:
    """Birden fazla dosyanın birleşik raporu."""
    files: list[str]
    total_rows: int
    column_report: ColumnReport
    df: pd.DataFrame
    stats: AggregatedStats
    prompt_context: str
    warnings: list[str] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────── #
# FileProcessor                                                          #
# ──────────────────────────────────────────────────────────────────── #

class FileProcessor:
    """
    Kullanım:
        fp = FileProcessor()
        report = fp.load_folder("Analizler")   # tek birleşik rapor
        # veya
        reports = fp.load_all(folder)          # dosya başına ayrı rapor
        merged  = fp.merge(reports)
    """

    def __init__(self, analizler_dir: str | Path = "Analizler"):
        self._base_dir = Path(analizler_dir)

    # ─────────────────────────────────────────── genel giriş noktaları ──

    def load_folder(self, folder: str | Path | None = None) -> MergedReport:
        """
        Belirtilen klasördeki tüm .csv / .xlsx / .xls dosyalarını okur
        ve tek bir MergedReport döndürür.
        """
        folder = Path(folder) if folder else self._base_dir
        if not folder.exists():
            raise FileNotFoundError(
                f"Klasör bulunamadı: {folder}\n"
                f"'{folder}' klasörünü oluşturup YouTube Studio'dan "
                "dışa aktarılan dosyaları içine kopyalayın."
            )

        files = sorted(
            [f for f in folder.iterdir()
             if f.suffix.lower() in {".csv", ".xlsx", ".xls"}]
        )
        if not files:
            raise ValueError(
                f"'{folder}' klasöründe CSV veya Excel dosyası bulunamadı."
            )

        reports = [self.load_file(f) for f in files]
        return self.merge(reports)

    def load_file(self, file_path: str | Path) -> FileReport:
        """Tek bir CSV veya Excel dosyasını okuyup FileReport döndürür."""
        path = Path(file_path)
        warnings: list[str] = []

        df_raw = _read_file(path)
        col_report, df_norm = _normalize_columns(df_raw)

        if col_report.missing_required:
            warnings.append(
                f"Zorunlu sütunlar eksik: {', '.join(col_report.missing_required)}"
            )

        df_clean = _clean_values(df_norm, warnings)
        df_filtered = _filter_key_columns(df_clean)

        stats: AggregatedStats | None = None
        if not df_filtered.empty and "views" in df_filtered.columns:
            stats = _compute_stats(df_filtered)

        context = _build_prompt_context(path.name, df_filtered, stats)

        return FileReport(
            file_path=str(path),
            file_name=path.name,
            rows_loaded=len(df_filtered),
            column_report=col_report,
            df=df_filtered,
            stats=stats,
            prompt_context=context,
            warnings=warnings,
        )

    def merge(self, reports: list[FileReport]) -> MergedReport:
        """Birden fazla FileReport'u birleştirir."""
        if not reports:
            raise ValueError("Birleştirilecek rapor yok.")

        dfs = [r.df for r in reports if not r.df.empty]
        merged_df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()

        # Birleşik sütun raporu
        all_found = list({c for r in reports for c in r.column_report.found})
        missing_req = [c for c in _KEY_METRICS if c not in all_found]
        missing_opt = [c for c in _OPTIONAL_METRICS if c not in all_found]
        col_report = ColumnReport(
            found=all_found,
            missing_required=missing_req,
            missing_optional=missing_opt,
            raw_to_standard={},
        )

        all_warnings = [w for r in reports for w in r.warnings]
        stats = _compute_stats(merged_df) if not merged_df.empty else None
        context = _build_prompt_context(
            f"{len(reports)} dosya birleşimi", merged_df, stats
        )

        return MergedReport(
            files=[r.file_name for r in reports],
            total_rows=len(merged_df),
            column_report=col_report,
            df=merged_df,
            stats=stats,
            prompt_context=context,
            warnings=all_warnings,
        )

    # ─────────────────────────────────────────────── yardımcı çıktılar ──

    def summary_table(self, report: FileReport | MergedReport) -> pd.DataFrame:
        """İnsan okunabilir özet DataFrame (terminale yazdırmak için)."""
        if report.stats is None:
            return pd.DataFrame()
        rows = []
        for v in report.stats.top_performers + report.stats.underperformers:
            rows.append({
                "Başlık": v.title[:55],
                "İzlenme": f"{v.views:,}",
                "CTR %": f"{v.ctr_pct:.1f}%",
                "Ort. İzleme": v.avg_watch_fmt,
                "İzleme Saati": f"{v.watch_time_hours:,.1f}",
                "Abone +/-": v.subscribers_gained,
            })
        return pd.DataFrame(rows)

    def export_summary(
        self,
        report: FileReport | MergedReport,
        output_path: str | Path,
    ) -> Path:
        """Özet tabloyu Excel veya CSV'ye aktarır."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        df = self.summary_table(report)
        if path.suffix == ".xlsx":
            df.to_excel(path, index=False, engine="openpyxl")
        else:
            df.to_csv(path, index=False, encoding="utf-8-sig")
        return path


# ──────────────────────────────────────────────────────────────────── #
# Dahili yardımcı fonksiyonlar                                          #
# ──────────────────────────────────────────────────────────────────── #

def _read_file(path: Path) -> pd.DataFrame:
    """Dosya uzantısına göre pandas ile okur."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".csv":
            # YouTube Studio bazı dosyaları BOM ile ihraç eder
            return pd.read_csv(path, encoding="utf-8-sig", thousands=".")
        elif suffix in {".xlsx", ".xls"}:
            return pd.read_excel(path, engine="openpyxl")
        else:
            raise ValueError(f"Desteklenmeyen dosya uzantısı: {suffix}")
    except Exception as e:
        raise IOError(f"Dosya okunamadı ({path.name}): {e}") from e


def _normalize_columns(df: pd.DataFrame) -> tuple[ColumnReport, pd.DataFrame]:
    """
    Ham sütun adlarını standart adlara çevirir.
    Büyük/küçük harf ve baştaki/sondaki boşlukları yoksayar.
    """
    raw_to_std: dict[str, str] = {}
    # Ham sütun adı → küçük harf temizlenmiş
    clean = {col: col.strip().lower() for col in df.columns}

    for std_name, variants in _COLUMN_MAP.items():
        for raw_col, clean_col in clean.items():
            if clean_col in variants and raw_col not in raw_to_std:
                raw_to_std[raw_col] = std_name
                break

    df_norm = df.rename(columns=raw_to_std)
    found = list(raw_to_std.values())
    missing_req = [c for c in _KEY_METRICS if c not in found]
    missing_opt = [c for c in _OPTIONAL_METRICS if c not in found]

    return (
        ColumnReport(
            found=found,
            missing_required=missing_req,
            missing_optional=missing_opt,
            raw_to_standard=raw_to_std,
        ),
        df_norm,
    )


def _clean_values(df: pd.DataFrame, warnings: list[str]) -> pd.DataFrame:
    """
    Sütun değerlerini temizler ve uygun tiplere dönüştürür.

    views             : "1.234" veya "1234" → int
    ctr               : "4,5%" veya "0.045" → float (yüzde)
    avg_watch_duration: "0:03:45" veya "225" → int saniye
    watch_time_hours  : "1.234,5" → float
    """
    df = df.copy()

    if "views" in df.columns:
        df["views"] = df["views"].apply(_parse_int)

    if "ctr" in df.columns:
        df["ctr"] = df["ctr"].apply(_parse_ctr_pct)

    if "avg_watch_duration" in df.columns:
        df["avg_watch_duration"] = df["avg_watch_duration"].apply(_parse_duration_seconds)

    if "watch_time_hours" in df.columns:
        df["watch_time_hours"] = df["watch_time_hours"].apply(_parse_float)

    for col in ["subscribers_gained", "likes", "comments", "shares", "impressions"]:
        if col in df.columns:
            df[col] = df[col].apply(_parse_int)

    # Başlık eksikse satır numarasını kullan
    if "title" not in df.columns:
        df["title"] = [f"Video {i+1}" for i in range(len(df))]
        warnings.append("'title' sütunu bulunamadı; satır numaraları kullanıldı.")

    # Geçersiz izlenme satırlarını düşür
    if "views" in df.columns:
        before = len(df)
        df = df[df["views"] >= 0].reset_index(drop=True)
        dropped = before - len(df)
        if dropped:
            warnings.append(f"{dropped} satır geçersiz izlenme değeri nedeniyle atlandı.")

    return df


def _filter_key_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Mevcut standart sütunları tutar, bilinmeyenleri atar."""
    keep = [c for c in _KEY_METRICS + _OPTIONAL_METRICS if c in df.columns]
    return df[keep].copy()


def _compute_stats(df: pd.DataFrame) -> AggregatedStats:
    """DataFrame'den toplu istatistikler hesaplar."""
    views = df["views"].fillna(0).astype(int)
    ctr = df["ctr"].fillna(0).astype(float) if "ctr" in df.columns else pd.Series([0.0] * len(df))
    dur = df["avg_watch_duration"].fillna(0).astype(int) if "avg_watch_duration" in df.columns else pd.Series([0] * len(df))
    wth = df["watch_time_hours"].fillna(0).astype(float) if "watch_time_hours" in df.columns else pd.Series([0.0] * len(df))
    subs = df["subscribers_gained"].fillna(0).astype(int) if "subscribers_gained" in df.columns else pd.Series([0] * len(df))

    # Etkileşim skoru: izlenme × CTR (tahmini erişim kalitesi)
    eng = (views * ctr / 100).round(1)

    rows: list[VideoSummaryRow] = []
    for i, row in df.iterrows():
        rows.append(VideoSummaryRow(
            title=str(row.get("title", f"Video {i+1}"))[:80],
            views=int(views.iloc[i]),
            ctr_pct=round(float(ctr.iloc[i]), 2),
            avg_watch_seconds=int(dur.iloc[i]),
            avg_watch_fmt=_fmt_seconds(int(dur.iloc[i])),
            watch_time_hours=round(float(wth.iloc[i]), 1),
            subscribers_gained=int(subs.iloc[i]),
            engagement_score=float(eng.iloc[i]),
        ))

    rows_by_views = sorted(rows, key=lambda r: r.views, reverse=True)
    top5 = rows_by_views[:5]
    bot5 = rows_by_views[-5:] if len(rows_by_views) > 5 else []

    # CTR dağılımı: <2% / 2-4% / 4-7% / >7%
    ctr_buckets = {"<%2": 0, "%2–4": 0, "%4–7": 0, ">%7": 0}
    for v in rows:
        if v.ctr_pct < 2:
            ctr_buckets["<%2"] += 1
        elif v.ctr_pct < 4:
            ctr_buckets["%2–4"] += 1
        elif v.ctr_pct < 7:
            ctr_buckets["%4–7"] += 1
        else:
            ctr_buckets[">%7"] += 1

    avg_dur = int(dur.mean()) if len(dur) else 0

    return AggregatedStats(
        total_videos=len(df),
        total_views=int(views.sum()),
        total_watch_time_hours=round(float(wth.sum()), 1),
        avg_views=round(float(views.mean()), 1),
        median_views=round(float(views.median()), 1),
        avg_ctr_pct=round(float(ctr.mean()), 2),
        median_ctr_pct=round(float(ctr.median()), 2),
        avg_watch_seconds=avg_dur,
        avg_watch_fmt=_fmt_seconds(avg_dur),
        total_subscribers_gained=int(subs.sum()),
        top_performers=top5,
        underperformers=bot5,
        ctr_distribution=ctr_buckets,
    )


def _build_prompt_context(
    source_label: str,
    df: pd.DataFrame,
    stats: AggregatedStats | None,
) -> str:
    """Claude'a gönderilmeye hazır, yapılandırılmış metin bağlamı üretir."""
    if stats is None or df.empty:
        return f"## Manuel Veri: {source_label}\n\nVeri bulunamadı veya işlenemedi."

    top_lines = "\n".join(
        f"  {i+1}. {v.title}\n"
        f"     İzlenme: {v.views:,} | CTR: %{v.ctr_pct} | "
        f"Ort. İzleme: {v.avg_watch_fmt} | İzleme Saati: {v.watch_time_hours:,.1f}"
        for i, v in enumerate(stats.top_performers)
    )
    bot_lines = "\n".join(
        f"  {i+1}. {v.title}\n"
        f"     İzlenme: {v.views:,} | CTR: %{v.ctr_pct} | Ort. İzleme: {v.avg_watch_fmt}"
        for i, v in enumerate(stats.underperformers)
    ) or "  (yeterli veri yok)"

    ctr_dist = "  |  ".join(
        f"{bucket}: {count} video"
        for bucket, count in stats.ctr_distribution.items()
        if count > 0
    )

    return dedent(f"""\
        ## Manuel Veri Analizi: {source_label}

        ### Genel Özet
        - Analiz edilen video sayısı : {stats.total_videos}
        - Toplam izlenme             : {stats.total_views:,}
        - Toplam izleme süresi       : {stats.total_watch_time_hours:,.1f} saat
        - Ortalama izlenme           : {stats.avg_views:,.0f}
        - Medyan izlenme             : {stats.median_views:,.0f}
        - Ortalama CTR               : %{stats.avg_ctr_pct}
        - Medyan CTR                 : %{stats.median_ctr_pct}
        - Ortalama izleme süresi     : {stats.avg_watch_fmt}
        - Toplam abone kazanımı      : {stats.total_subscribers_gained:+,}

        ### CTR Dağılımı
        {ctr_dist}

        ### En Çok İzlenen 5 Video
        {top_lines}

        ### En Az İzlenen 5 Video (Düzeltme Adayları)
        {bot_lines}
    """).strip()


# ──────────────────────────────────────────────────────────────────── #
# Dönüşüm yardımcıları                                                  #
# ──────────────────────────────────────────────────────────────────── #

def _parse_int(val: Any) -> int:
    if pd.isna(val):
        return 0
    s = str(val).strip().replace(".", "").replace(",", "").replace(" ", "")
    m = re.search(r"-?\d+", s)
    return int(m.group()) if m else 0


def _parse_float(val: Any) -> float:
    if pd.isna(val):
        return 0.0
    s = str(val).strip().replace(" ", "")
    # Türkçe ondalık: "1.234,56" → "1234.56"
    if re.search(r"\d\.\d{3},", s):
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    m = re.search(r"-?\d+\.?\d*", s)
    return float(m.group()) if m else 0.0


def _parse_ctr_pct(val: Any) -> float:
    """
    YouTube Studio CTR değerlerini yüzdeye dönüştürür.
    "4,5%"  → 4.5
    "0.045" → 4.5   (ondalık oran ise ×100)
    "4.5%"  → 4.5
    """
    if pd.isna(val):
        return 0.0
    s = str(val).strip()
    has_pct = "%" in s
    s_clean = s.replace("%", "").replace(",", ".").strip()
    try:
        v = float(s_clean)
    except ValueError:
        return 0.0
    # Ondalık oran olarak geliyorsa (0.0–1.0) yüzeye çevir
    if not has_pct and v <= 1.0:
        v *= 100
    return round(v, 4)


def _parse_duration_seconds(val: Any) -> int:
    """
    "0:03:45" → 225
    "3:45"    → 225
    "225"     → 225
    """
    if pd.isna(val):
        return 0
    s = str(val).strip()
    # SS:DD veya SS:SS:DD formatı
    parts = s.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        else:
            return int(float(s))
    except (ValueError, IndexError):
        return 0


def _fmt_seconds(total: int) -> str:
    """225 → "3:45" """
    if total <= 0:
        return "0:00"
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"
