"""
src/gui_app.py  —  yt-manual-analyzer grafiksel arayüzü
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Başlatmak için:
    python main.py gui
    python -m src.gui_app
    python src/gui_app.py

Gereksinim: pip install customtkinter
"""

from __future__ import annotations

import os
import queue
import threading
import traceback
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from dotenv import load_dotenv

load_dotenv()

# ── Tema ─────────────────────────────────────────────────────────────── #
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

_PAD = 16

_C = {
    "bg":        "#1a1a2e",
    "sidebar":   "#16213e",
    "card":      "#0f3460",
    "deep":      "#0a2540",
    "active":    "#1f6aa5",
    "hover":     "#2d7fc1",
    "idle":      "#1e2a3a",
    "text":      "#e0e0e0",
    "dim":       "#7a8fa6",
    "border":    "#1f3a5f",
    "ok":        "#2ecc71",
    "warn":      "#f39c12",
    "err":       "#e74c3c",
}


# ─────────────────────────────────────────────────────────────────────── #
#  Genel yardımcılar                                                       #
# ─────────────────────────────────────────────────────────────────────── #

def _in_thread(fn, *args, **kwargs) -> threading.Thread:
    """Fonksiyonu daemon arka-plan thread'inde çalıştırır."""
    t = threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True)
    t.start()
    return t


# ─────────────────────────────────────────────────────────────────────── #
#  Sidebar butonu                                                           #
# ─────────────────────────────────────────────────────────────────────── #

class _NavBtn(ctk.CTkButton):
    def __init__(self, parent, label: str, icon: str, on_click, **kw):
        super().__init__(
            parent,
            text=f"  {icon}  {label}",
            anchor="w",
            height=44,
            corner_radius=8,
            border_width=0,
            fg_color=_C["idle"],
            hover_color=_C["hover"],
            text_color=_C["text"],
            font=ctk.CTkFont(size=14),
            command=on_click,
            **kw,
        )

    def activate(self, flag: bool) -> None:
        self.configure(fg_color=_C["active"] if flag else _C["idle"])


# ─────────────────────────────────────────────────────────────────────── #
#  Ortak panel tabanı                                                       #
# ─────────────────────────────────────────────────────────────────────── #

class _Panel(ctk.CTkFrame):
    def __init__(self, parent, **kw):
        super().__init__(parent, fg_color=_C["bg"], **kw)

    # ── küçük fabrikalar ──────────────────────────────────────────────

    def _label(self, text: str, size: int = 12, bold: bool = False,
               color: str | None = None) -> ctk.CTkLabel:
        return ctk.CTkLabel(
            self, text=text,
            font=ctk.CTkFont(size=size, weight="bold" if bold else "normal"),
            text_color=color or _C["dim"],
        )

    def _card(self) -> ctk.CTkFrame:
        return ctk.CTkFrame(
            self, fg_color=_C["card"],
            corner_radius=10, border_width=1, border_color=_C["border"],
        )

    def _textbox(self, parent=None) -> ctk.CTkTextbox:
        box = ctk.CTkTextbox(
            parent or self,
            font=ctk.CTkFont(family="Courier", size=12),
            text_color=_C["text"],
            fg_color=_C["deep"],
            border_color=_C["border"],
            border_width=1,
            wrap="word",
            state="disabled",
        )
        return box

    def _progress(self, parent=None) -> ctk.CTkProgressBar:
        return ctk.CTkProgressBar(
            parent or self,
            mode="indeterminate",
            progress_color=_C["active"],
            fg_color=_C["card"],
            height=7,
        )

    # ── textbox yazma (thread-safe) ──────────────────────────────────

    def _append(self, box: ctk.CTkTextbox, text: str) -> None:
        box.configure(state="normal")
        box.insert("end", text)
        box.see("end")
        box.configure(state="disabled")

    def _set_text(self, box: ctk.CTkTextbox, text: str) -> None:
        box.configure(state="normal")
        box.delete("0.0", "end")
        box.insert("end", text)
        box.see("0.0")
        box.configure(state="disabled")

    def _clear(self, box: ctk.CTkTextbox) -> None:
        box.configure(state="normal")
        box.delete("0.0", "end")
        box.configure(state="disabled")


# ─────────────────────────────────────────────────────────────────────── #
#  VERİ ANALİZİ PANELİ                                                     #
# ─────────────────────────────────────────────────────────────────────── #

class DataPanel(_Panel):
    """
    Seçilen CSV / Excel dosyasını ya da Analizler/ klasörünü FileProcessor
    ile okur, AggregatedStats'ı metin kutusuna yazar ve AIStrategist'i
    çağırır.  Tüm I/O arka-plan thread'inde; GUI hiç donmaz.
    """

    def __init__(self, parent, status: "_StatusBar", **kw):
        super().__init__(parent, **kw)
        self._status = status
        self._selected_file: Path | None = None
        self._q: queue.Queue = queue.Queue()
        self._build()
        self._poll()         # 100 ms'de bir kuyruk tüketir

    # ── arayüz inşası ────────────────────────────────────────────────

    def _build(self) -> None:
        # Başlık
        ctk.CTkLabel(self, text="Veri Analizi",
                     font=ctk.CTkFont(size=22, weight="bold"),
                     text_color=_C["text"]).pack(
            anchor="w", padx=_PAD, pady=(_PAD, 2))
        self._label(
            "YouTube Studio'dan indirdiğiniz CSV / Excel dosyasını "
            "seçin ya da Analizler/ klasörünü doğrudan analiz edin."
        ).pack(anchor="w", padx=_PAD, pady=(0, _PAD))

        # ── Dosya seçim satırı ───────────────────────────────────── #
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=_PAD, pady=(0, 6))

        self._file_lbl = ctk.CTkLabel(
            row, text="Henüz dosya seçilmedi",
            font=ctk.CTkFont(size=12), text_color=_C["dim"], anchor="w",
        )
        self._file_lbl.pack(side="left", fill="x", expand=True)

        ctk.CTkButton(
            row, text="📂  Gözat", width=110, height=34,
            fg_color=_C["active"], hover_color=_C["hover"],
            font=ctk.CTkFont(size=13), command=self._browse,
        ).pack(side="right", padx=(8, 0))

        # ── Analiz butonu (tek dosya) ────────────────────────────── #
        self._btn_file = ctk.CTkButton(
            self, text="▶  Seçili Dosyayı Analiz Et",
            height=40, state="disabled",
            fg_color=_C["active"], hover_color=_C["hover"],
            font=ctk.CTkFont(size=13, weight="bold"),
            command=lambda: self._start(folder_mode=False),
        )
        self._btn_file.pack(fill="x", padx=_PAD, pady=(0, 6))

        # ── Klasör analizi butonu ─────────────────────────────────── #
        ctk.CTkButton(
            self, text="📁  Analizler/ Klasörünü Analiz Et",
            height=40,
            fg_color=_C["idle"], hover_color=_C["hover"],
            font=ctk.CTkFont(size=13),
            command=lambda: self._start(folder_mode=True),
        ).pack(fill="x", padx=_PAD, pady=(0, _PAD))

        # ── İlerleme barı ─────────────────────────────────────────── #
        self._bar = self._progress()
        self._bar.pack(fill="x", padx=_PAD, pady=(0, 6))
        self._bar.set(0)

        # ── Sonuç kutusu ─────────────────────────────────────────── #
        self._label("Analiz Sonuçları", bold=True).pack(
            anchor="w", padx=_PAD, pady=(4, 4))
        self._out = self._textbox()
        self._out.pack(fill="both", expand=True, padx=_PAD, pady=(0, _PAD))

    # ── Olaylar ──────────────────────────────────────────────────────

    def _browse(self) -> None:
        path = filedialog.askopenfilename(
            title="CSV / Excel dosyası seçin",
            filetypes=[
                ("Desteklenen dosyalar", "*.csv *.xlsx *.xls"),
                ("CSV", "*.csv"), ("Excel", "*.xlsx *.xls"),
                ("Tüm dosyalar", "*.*"),
            ],
        )
        if path:
            self._selected_file = Path(path)
            self._file_lbl.configure(
                text=self._selected_file.name, text_color=_C["text"])
            self._btn_file.configure(state="normal")

    def _start(self, folder_mode: bool) -> None:
        if not folder_mode and self._selected_file is None:
            messagebox.showwarning("Dosya Seçin", "Önce bir dosya seçin.")
            return
        self._btn_file.configure(state="disabled")
        self._bar.configure(mode="indeterminate")
        self._bar.start()
        self._status.set("Dosyalar okunuyor…", "info")
        self._clear(self._out)
        _in_thread(self._worker, folder_mode=folder_mode)

    # ── Arka-plan işçisi ──────────────────────────────────────────────
    #
    #  Tüm FileProcessor ve AIStrategist çağrıları burada çalışır.
    #  GUI thread'i hiç bloklenmez; sonuçlar queue üzerinden iletilir.
    # ──────────────────────────────────────────────────────────────────

    def _worker(self, folder_mode: bool) -> None:
        try:
            # Geç import — başlangıçta customtkinter ile çakışmayı önler
            from src.file_processor import FileProcessor
            from src.ai_strategist import AIStrategist, QueryType

            fp = FileProcessor()
            self._q.put(("log", "📂  Dosyalar okunuyor…\n"))

            # ── 1. Dosya / klasör okuma ──────────────────────────── #
            if folder_mode:
                merged = fp.load_folder("Analizler")
            else:
                report = fp.load_file(self._selected_file)  # type: ignore[arg-type]
                merged = fp.merge([report])

            # Uyarıları ilet
            for w in merged.warnings:
                self._q.put(("log", f"⚠  {w}\n"))

            if merged.stats is None:
                self._q.put(("error",
                    "Geçerli veri bulunamadı.\n"
                    "Beklenen sütunlar: Video başlığı, İzlenme sayısı, "
                    "Tıklama oranı (TO), Ortalama izleme süresi"))
                return

            # ── 2. İstatistik özetini oluştur ───────────────────── #
            s = merged.stats
            sep = "─" * 52

            lines: list[str] = [
                f"\n{sep}",
                f"  Analiz Raporu  —  {len(merged.files)} dosya, "
                f"{merged.total_rows} video",
                sep,
                f"  Toplam video          : {s.total_videos}",
                f"  Toplam izlenme        : {s.total_views:,}",
                f"  Toplam izleme süresi  : {s.total_watch_time_hours:,.1f} saat",
                f"  Ortalama izlenme      : {s.avg_views:,.0f}",
                f"  Medyan izlenme        : {s.median_views:,.0f}",
                f"  Ortalama CTR          : %{s.avg_ctr_pct:.1f}",
                f"  Medyan CTR            : %{s.median_ctr_pct:.1f}",
                f"  Ort. izleme süresi    : {s.avg_watch_fmt}",
                f"  Toplam abone değişimi : {s.total_subscribers_gained:+,}",
            ]

            # CTR dağılımı
            if s.ctr_distribution:
                lines.append(f"\n  CTR Dağılımı:")
                for bucket, count in s.ctr_distribution.items():
                    lines.append(f"    {bucket}: {count} video")

            # En iyi videolar
            if s.top_performers:
                lines.append(f"\n  En Çok İzlenen Videolar:")
                for i, v in enumerate(s.top_performers, 1):
                    lines.append(
                        f"    {i}. {v.title[:52]}")
                    lines.append(
                        f"       {v.views:,} izl.  ·  "
                        f"CTR %{v.ctr_pct:.1f}  ·  "
                        f"Ort. {v.avg_watch_fmt}")

            # Gelişim fırsatları
            if s.underperformers:
                lines.append(f"\n  Gelişim Fırsatları (En Az İzlenen):")
                for i, v in enumerate(s.underperformers, 1):
                    lines.append(
                        f"    {i}. {v.title[:52]}")
                    lines.append(
                        f"       {v.views:,} izl.  ·  "
                        f"CTR %{v.ctr_pct:.1f}")

            lines.append(f"{sep}\n")
            self._q.put(("log", "\n".join(lines)))

            # ── 3. AI strateji analizi ───────────────────────────── #
            self._q.put(("log", "🤖  AI strateji analizi yapılıyor…\n"))

            strategist = AIStrategist(period_label="Son dönem")
            report = strategist.analyze(merged, query=QueryType.CONTENT_FOCUS)

            ai_lines: list[str] = [sep, "  AI Strateji İçgörüleri", sep]

            if report.strengths:
                ai_lines.append("\n  Güçlü Yönler:")
                for item in report.strengths[:6]:
                    ai_lines.append(f"  ✓  {item}")

            if report.primary_recommendation:
                ai_lines.append(
                    f"\n  Birincil Öneri:\n  →  {report.primary_recommendation}")

            if report.action_items:
                ai_lines.append("\n  Bu Hafta Yapılacaklar:")
                for i, item in enumerate(report.action_items[:6], 1):
                    ai_lines.append(f"  {i}.  {item}")

            ai_lines.append(f"\n{sep}\n")
            self._q.put(("log", "\n".join(ai_lines)))
            self._q.put(("done", None))

        except FileNotFoundError as exc:
            self._q.put(("error", str(exc)))
        except Exception:
            self._q.put(("error", traceback.format_exc()))

    # ── Queue tüketici (GUI thread — 100 ms döngüsü) ─────────────────

    def _poll(self) -> None:
        try:
            while True:
                kind, data = self._q.get_nowait()
                if kind == "log":
                    self._append(self._out, data)
                elif kind == "done":
                    self._bar.stop()
                    self._bar.set(1)
                    self._btn_file.configure(state="normal")
                    self._status.set("Analiz tamamlandı ✓", "ok")
                elif kind == "error":
                    self._append(self._out, f"\n❌  Hata:\n{data}\n")
                    self._bar.stop()
                    self._bar.set(0)
                    self._btn_file.configure(state="normal")
                    self._status.set("Hata oluştu", "err")
        except queue.Empty:
            pass
        self.after(100, self._poll)


# ─────────────────────────────────────────────────────────────────────── #
#  SENARYO YAZARI PANELİ                                                   #
# ─────────────────────────────────────────────────────────────────────── #

class ScriptPanel(_Panel):
    """
    WriterEngine'in 4 pasını arka-plan thread'inde sırayla çalıştırır.

    Adım sinyalleri (step 0-3) her _pass_* çağrısından ÖNCE queue'ya eklenir.
    GUI thread'i sinyali görünce adım göstergesini günceller — bu sayede
    kullanıcı hangi adımın çalıştığını gerçek zamanlı takip eder.

    Monkey-patch kullanılmaz: WriterEngine'in private metodları doğrudan
    çağrılır ve sonuç nesnesi (ScriptDraft) elle oluşturulur.
    """

    _STEP_LABELS = [
        "1. Taslak",
        "2. İddiaları Çıkar",
        "3. Çapraz Doğrula",
        "4. Düzelt & Tamamla",
    ]
    _STEP_STATUS = [
        "Taslak üretiliyor… (60-90 sn)",
        "İddialar çıkarılıyor…",
        "Çapraz doğrulama yapılıyor…",
        "Düzeltmeler uygulanıyor…",
    ]

    def __init__(self, parent, status: "_StatusBar", **kw):
        super().__init__(parent, **kw)
        self._status = status
        self._q: queue.Queue = queue.Queue()
        self._script_content: str = ""
        self._build()
        self._poll()

    # ── arayüz inşası ────────────────────────────────────────────────

    def _build(self) -> None:
        # Başlık
        ctk.CTkLabel(self, text="Senaryo Yazarı",
                     font=ctk.CTkFont(size=22, weight="bold"),
                     text_color=_C["text"]).pack(
            anchor="w", padx=_PAD, pady=(_PAD, 2))
        self._label(
            "4 aşamalı pipeline: Taslak → İddia Çıkarımı "
            "→ Çapraz Doğrulama → Düzeltme & Uzunluk Garantisi"
        ).pack(anchor="w", padx=_PAD, pady=(0, _PAD))

        # ── Giriş kartı ──────────────────────────────────────────── #
        card = self._card()
        card.pack(fill="x", padx=_PAD, pady=(0, _PAD))

        self._label("Konu Başlığı *", bold=True).pack(
            anchor="w", in_=card, padx=12, pady=(12, 2))
        self._topic = ctk.CTkEntry(
            card, placeholder_text="Örn: Kuantum bilgisayarların geleceği",
            height=40, font=ctk.CTkFont(size=13),
            fg_color=_C["deep"], border_color=_C["border"],
        )
        self._topic.pack(fill="x", padx=12, pady=(0, 8))

        self._label(
            "Video Başlığı  (boş bırakılırsa konu başlığı kullanılır)",
            bold=True,
        ).pack(anchor="w", in_=card, padx=12, pady=(4, 2))
        self._title = ctk.CTkEntry(
            card,
            placeholder_text="Örn: Kuantum Bilgisayarlar Hayatımızı Nasıl Değiştirecek?",
            height=40, font=ctk.CTkFont(size=13),
            fg_color=_C["deep"], border_color=_C["border"],
        )
        self._title.pack(fill="x", padx=12, pady=(0, 8))

        self._label("Ek Yönergeler  (isteğe bağlı)", bold=True).pack(
            anchor="w", in_=card, padx=12, pady=(4, 2))
        self._extra = ctk.CTkTextbox(
            card, height=68, font=ctk.CTkFont(size=12),
            fg_color=_C["deep"], border_color=_C["border"], border_width=1,
        )
        self._extra.pack(fill="x", padx=12, pady=(0, 12))

        # ── Oluştur butonu ───────────────────────────────────────── #
        self._btn_gen = ctk.CTkButton(
            self,
            text="✍  12.000 Karakterlik Senaryo Oluştur",
            height=48,
            fg_color=_C["active"], hover_color=_C["hover"],
            font=ctk.CTkFont(size=15, weight="bold"),
            command=self._start,
        )
        self._btn_gen.pack(fill="x", padx=_PAD, pady=(0, 8))

        # ── İlerleme barı + durum etiketi ────────────────────────── #
        prog_row = ctk.CTkFrame(self, fg_color="transparent")
        prog_row.pack(fill="x", padx=_PAD, pady=(0, 6))

        self._bar = self._progress(prog_row)
        self._bar.pack(side="left", fill="x", expand=True)
        self._bar.set(0)

        self._prog_lbl = ctk.CTkLabel(
            prog_row, text="", width=190,
            font=ctk.CTkFont(size=11), text_color=_C["dim"],
        )
        self._prog_lbl.pack(side="right", padx=(8, 0))

        # ── Adım göstergesi (4 kutu) ─────────────────────────────── #
        step_row = ctk.CTkFrame(self, fg_color="transparent")
        step_row.pack(fill="x", padx=_PAD, pady=(0, 8))
        self._step_btns: list[ctk.CTkLabel] = []
        for lbl in self._STEP_LABELS:
            box = ctk.CTkLabel(
                step_row, text=lbl,
                font=ctk.CTkFont(size=11),
                text_color=_C["dim"],
                fg_color=_C["card"],
                corner_radius=6,
                padx=8, pady=4,
            )
            box.pack(side="left", padx=(0, 6))
            self._step_btns.append(box)

        # ── Çıktı araç çubuğu ────────────────────────────────────── #
        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.pack(fill="x", padx=_PAD, pady=(0, 4))

        self._label("Üretilen Senaryo", bold=True).pack(side="left", in_=toolbar)

        for icon, tip, fn in [
            ("📋  Kopyala", "Panoya kopyala", self._copy),
            ("💾  Kaydet",  "Farklı kaydet",  self._save),
        ]:
            ctk.CTkButton(
                toolbar, text=icon, width=110, height=28,
                fg_color=_C["idle"], hover_color=_C["hover"],
                font=ctk.CTkFont(size=12), command=fn,
            ).pack(side="right", padx=(0, 6))

        # ── Senaryo metin kutusu ─────────────────────────────────── #
        self._out = self._textbox()
        self._out.pack(fill="both", expand=True, padx=_PAD, pady=(0, _PAD))

    # ── İç yardımcı: adım renklendirme ──────────────────────────────

    def _highlight_step(self, active: int) -> None:
        for i, box in enumerate(self._step_btns):
            if i < active:                          # tamamlandı
                box.configure(text_color=_C["ok"], fg_color=_C["card"])
            elif i == active:                       # şu an çalışıyor
                box.configure(text_color="#ffffff", fg_color=_C["active"])
            else:                                   # bekleniyor
                box.configure(text_color=_C["dim"], fg_color=_C["card"])

    def _reset_steps(self) -> None:
        for box in self._step_btns:
            box.configure(text_color=_C["dim"], fg_color=_C["card"])

    # ── Buton eylemleri ──────────────────────────────────────────────

    def _copy(self) -> None:
        text = self._script_content
        if not text:
            messagebox.showinfo("Boş", "Kopyalanacak senaryo yok.")
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self._status.set("Senaryo panoya kopyalandı ✓", "ok")

    def _save(self) -> None:
        if not self._script_content:
            messagebox.showinfo("Boş", "Kaydedilecek senaryo yok.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".md",
            filetypes=[("Markdown", "*.md"), ("Metin", "*.txt"),
                       ("Tüm dosyalar", "*.*")],
            initialfile=f"senaryo_{datetime.now().strftime('%Y%m%d_%H%M')}.md",
        )
        if path:
            Path(path).write_text(self._script_content, encoding="utf-8")
            self._status.set(f"Kaydedildi → {Path(path).name}", "ok")

    # ── İşlemi başlat ────────────────────────────────────────────────

    def _start(self) -> None:
        topic = self._topic.get().strip()
        if not topic:
            messagebox.showwarning("Eksik Bilgi", "Konu başlığını girin.")
            return

        title = self._title.get().strip() or topic
        extra = self._extra.get("0.0", "end").strip()

        self._btn_gen.configure(state="disabled")
        self._bar.configure(mode="indeterminate")
        self._bar.start()
        self._reset_steps()
        self._clear(self._out)
        self._script_content = ""
        self._status.set("Senaryo pipeline'ı başlatıldı…", "info")
        _in_thread(self._worker, topic=topic, title=title, extra=extra)

    # ── Arka-plan işçisi ──────────────────────────────────────────────
    #
    #  WriterEngine'in dört pasını SIRAYLA çağırır.
    #  Her çağrıdan önce queue'ya ("step", i) sinyali eklenir →
    #  GUI thread'i adım göstergesini günceller.
    #
    #  Bu yaklaşım monkey-patch kullanmaz, @retry decorator'larına
    #  dokunmaz ve WriterEngine.write() metodundan bağımsızdır.
    # ──────────────────────────────────────────────────────────────────

    def _worker(self, topic: str, title: str, extra: str) -> None:
        try:
            # Geç import — GUI başlamadan önce bu modülleri yüklememek
            # için worker içinde import ediyoruz.  Thread-safe'tir.
            from src.writer_engine import (
                WriterEngine,
                ScriptDraft,
                _detect_domains,
                _measure_budgets,
                _parse_input,
            )

            engine = WriterEngine()

            # Girişi ayrıştır  (str → topic / strategy_ctx / source_type)
            topic_str, strategy_ctx, source_type = _parse_input(topic)
            title_str = title or topic_str
            domains   = _detect_domains(topic_str + " " + title_str)

            # ── Pas 1: Taslak ────────────────────────────────────── #
            self._q.put(("step", 0))
            draft = engine._pass_draft(
                topic_str, title_str, domains, strategy_ctx, extra)

            # ── Pas 2: İddia çıkarımı ────────────────────────────── #
            self._q.put(("step", 1))
            claims = engine._pass_extract_claims(draft, domains)

            # ── Pas 3: Çapraz doğrulama ──────────────────────────── #
            self._q.put(("step", 2))
            verification = engine._pass_verify(draft, claims, domains)

            # ── Pas 4: Düzeltme + uzunluk garantisi ──────────────── #
            self._q.put(("step", 3))
            final_text, passes = engine._pass_correct(
                draft, verification, topic_str, title_str)

            # ── ScriptDraft oluştur ve kaydet ─────────────────────── #
            budgets = _measure_budgets(final_text)
            script  = ScriptDraft(
                topic=topic_str,
                title=title_str,
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
            script.saved_path = engine.save(script)

            self._q.put(("result", script))

        except EnvironmentError as exc:
            # Büyük ihtimalle ANTHROPIC_API_KEY eksik
            self._q.put(("error",
                f"API anahtarı bulunamadı:\n{exc}\n\n"
                "Ayarlar sekmesine gidip ANTHROPIC_API_KEY değerini girin."))
        except Exception:
            self._q.put(("error", traceback.format_exc()))

    # ── Queue tüketici (GUI thread) ──────────────────────────────────

    def _poll(self) -> None:
        try:
            while True:
                kind, data = self._q.get_nowait()

                if kind == "step":
                    self._highlight_step(data)
                    self._prog_lbl.configure(text=self._STEP_STATUS[data])
                    self._status.set(self._STEP_STATUS[data], "info")

                elif kind == "result":
                    script: ScriptDraft = data
                    self._bar.stop()
                    self._bar.set(1)
                    self._btn_gen.configure(state="normal")
                    # tüm adımları yeşil yap
                    for box in self._step_btns:
                        box.configure(text_color=_C["ok"], fg_color=_C["card"])
                    self._prog_lbl.configure(text="Tamamlandı ✓")

                    len_ok = script.meets_length
                    ver_ok = script.verification.is_clean
                    sep    = "─" * 54

                    header = (
                        f"\n{sep}\n"
                        f"  {'✓' if len_ok else '⚠'} "
                        f"{script.char_count:,} karakter  ·  "
                        f"{script.word_count:,} kelime  ·  "
                        f"{script.passes_completed} pas tamamlandı\n"
                        f"  {'✓' if ver_ok else '⚠'} "
                        f"Doğruluk skoru: {script.verification.score}/100  ·  "
                        f"{script.verification.claims_extracted} iddia incelendi\n"
                    )
                    if script.domains:
                        header += f"  Alan(lar): {', '.join(script.domains)}\n"
                    if script.saved_path:
                        header += f"  Kaydedildi → {script.saved_path}\n"

                    # Bölüm bütçe özeti
                    header += f"\n  Bölüm Bütçeleri:\n"
                    for b in script.section_budgets:
                        icon = {"hedefte": "✓", "kısa": "▲",
                                "uzun": "▼", "eksik": "✗"}.get(b.status, "?")
                        header += (
                            f"    {icon} {b.label:<12} "
                            f"{b.target_min:,}–{b.target_max:,} kr  →  "
                            f"{b.actual:,} kr  [{b.status}]\n"
                        )
                    header += f"{sep}\n\n"

                    full_text = header + script.content
                    self._script_content = script.content
                    self._set_text(self._out, full_text)

                    status_msg = (
                        f"Senaryo tamamlandı ✓  —  "
                        f"{script.char_count:,} karakter, "
                        f"doğruluk {script.verification.score}/100"
                    )
                    self._status.set(status_msg, "ok")

                elif kind == "error":
                    self._bar.stop()
                    self._bar.set(0)
                    self._btn_gen.configure(state="normal")
                    self._prog_lbl.configure(text="Hata")
                    self._set_text(self._out, f"❌  Hata:\n\n{data}")
                    self._status.set("Senaryo oluşturulamadı", "err")

        except queue.Empty:
            pass
        self.after(100, self._poll)


# ─────────────────────────────────────────────────────────────────────── #
#  AYARLAR PANELİ                                                          #
# ─────────────────────────────────────────────────────────────────────── #

class SettingsPanel(_Panel):
    _FIELDS = [
        ("ANTHROPIC_API_KEY",   "Anthropic API Anahtarı *",      True),
        ("YOUTUBE_API_KEY",     "YouTube API Anahtarı (opsiyonel)", False),
        ("CLAUDE_MODEL",        "Claude Modeli",                  False),
        ("MAX_TOKENS",          "Maks. Token (genel)",            False),
        ("SCENARIO_MAX_TOKENS", "Maks. Token (senaryo)",          False),
        ("OUTPUT_DIR",          "Çıktı Klasörü",                  False),
    ]

    def __init__(self, parent, status: "_StatusBar", **kw):
        super().__init__(parent, **kw)
        self._status = status
        self._entries: dict[str, ctk.CTkEntry] = {}
        self._build()

    def _build(self) -> None:
        ctk.CTkLabel(self, text="Ayarlar",
                     font=ctk.CTkFont(size=22, weight="bold"),
                     text_color=_C["text"]).pack(
            anchor="w", padx=_PAD, pady=(_PAD, 2))
        self._label(
            "API anahtarları ve model tercihlerini yapılandırın. "
            "Kaydet'e basıldığında .env dosyasına yazılır ve "
            "ortam değişkenleri anında güncellenir."
        ).pack(anchor="w", padx=_PAD, pady=(0, _PAD))

        # ── API anahtar kartı ─────────────────────────────────────── #
        card = self._card()
        card.pack(fill="x", padx=_PAD, pady=(0, _PAD))

        for env_key, label, required in self._FIELDS:
            self._label(
                label + (" *" if required else ""), bold=True
            ).pack(anchor="w", in_=card, padx=12, pady=(10, 2))

            entry = ctk.CTkEntry(
                card, height=38, font=ctk.CTkFont(size=12),
                fg_color=_C["deep"], border_color=_C["border"],
                show="*" if "KEY" in env_key else "",
            )
            val = os.getenv(env_key, "")
            if val:
                entry.insert(0, val)
            entry.pack(fill="x", padx=12, pady=(0, 4))
            self._entries[env_key] = entry

        ctk.CTkButton(
            card, text="💾  .env Dosyasına Kaydet ve Uygula",
            height=42, fg_color=_C["active"], hover_color=_C["hover"],
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._save_env,
        ).pack(fill="x", padx=12, pady=(8, 12))

        # ── Model bilgi kartı ─────────────────────────────────────── #
        info = self._card()
        info.pack(fill="x", padx=_PAD, pady=(0, _PAD))

        self._label("Kullanılabilir Claude Modelleri", bold=True).pack(
            anchor="w", in_=info, padx=12, pady=(12, 6))

        models = [
            ("claude-sonnet-4-6",           "Önerilen — hız / kalite dengesi"),
            ("claude-opus-4-7",             "Maksimum kalite — daha yavaş"),
            ("claude-haiku-4-5-20251001",   "En hızlı — kısa içerikler için"),
        ]
        for model_id, desc in models:
            row = ctk.CTkFrame(info, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=2)
            ctk.CTkLabel(
                row, text=model_id,
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=_C["text"],
            ).pack(side="left")
            ctk.CTkLabel(
                row, text=f"  —  {desc}",
                font=ctk.CTkFont(size=12), text_color=_C["dim"],
            ).pack(side="left")
        ctk.CTkLabel(info, text="").pack(pady=6)

    def _save_env(self) -> None:
        env_path = Path(".env")
        new_vals = {k: e.get().strip() for k, e in self._entries.items()}

        # Mevcut satırları oku; bilinen anahtarları güncelle
        existing_lines: list[str] = []
        updated_keys: set[str] = set()

        if env_path.exists():
            for raw in env_path.read_text(encoding="utf-8").splitlines():
                key = raw.split("=", 1)[0].strip()
                if key in new_vals:
                    val = new_vals[key]
                    if val:
                        existing_lines.append(f"{key}={val}")
                    updated_keys.add(key)
                else:
                    existing_lines.append(raw)

        # Yeni anahtarları ekle
        for key, val in new_vals.items():
            if key not in updated_keys and val:
                existing_lines.append(f"{key}={val}")

        env_path.write_text("\n".join(existing_lines) + "\n", encoding="utf-8")

        # Ortam değişkenlerini anında güncelle
        for key, val in new_vals.items():
            if val:
                os.environ[key] = val

        load_dotenv(override=True)
        self._status.set(".env kaydedildi ve ortam değişkenleri güncellendi ✓", "ok")


# ─────────────────────────────────────────────────────────────────────── #
#  Durum çubuğu (pencere alt kısmı)                                        #
# ─────────────────────────────────────────────────────────────────────── #

class _StatusBar(ctk.CTkFrame):
    _COLORS = {"ok": "#2ecc71", "warn": "#f39c12",
               "err": "#e74c3c", "info": "#7a8fa6"}

    def __init__(self, parent, **kw):
        super().__init__(parent, fg_color=_C["sidebar"],
                         height=30, corner_radius=0, **kw)
        self._lbl = ctk.CTkLabel(
            self, text="Hazır",
            font=ctk.CTkFont(size=11), text_color=_C["dim"])
        self._lbl.pack(side="left", padx=12, pady=4)

    def set(self, text: str, kind: str = "info") -> None:
        self._lbl.configure(
            text=text, text_color=self._COLORS.get(kind, _C["dim"]))


# ─────────────────────────────────────────────────────────────────────── #
#  Ana Uygulama Penceresi                                                   #
# ─────────────────────────────────────────────────────────────────────── #

class App(ctk.CTk):
    _NAV = [
        ("Veri Analizi",   "📊", DataPanel),
        ("Senaryo Yazarı", "✍",  ScriptPanel),
        ("Ayarlar",        "⚙",  SettingsPanel),
    ]

    def __init__(self):
        super().__init__()
        self.title("yt-manual-analyzer")
        self.geometry("1110x740")
        self.minsize(880, 580)
        self.configure(fg_color=_C["bg"])
        self._build()

    def _build(self) -> None:
        # Durum çubuğu (altta sabit)
        self._status = _StatusBar(self)
        self._status.pack(side="bottom", fill="x")

        # Ana container
        root = ctk.CTkFrame(self, fg_color=_C["bg"])
        root.pack(fill="both", expand=True)

        # ── Sidebar ──────────────────────────────────────────────── #
        sidebar = ctk.CTkFrame(root, width=204,
                               fg_color=_C["sidebar"], corner_radius=0)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        ctk.CTkLabel(sidebar,
                     text="yt-manual\nanalyzer",
                     font=ctk.CTkFont(size=16, weight="bold"),
                     text_color=_C["text"], justify="left"
                     ).pack(anchor="w", padx=16, pady=(20, 2))
        ctk.CTkLabel(sidebar,
                     text="Manuel Analiz  ×  Claude API",
                     font=ctk.CTkFont(size=10), text_color=_C["dim"]
                     ).pack(anchor="w", padx=16, pady=(0, 16))
        ctk.CTkFrame(sidebar, height=1,
                     fg_color=_C["border"]).pack(fill="x", padx=12)

        self._nav_btns: list[_NavBtn] = []
        self._panels:   dict[str, _Panel] = {}
        content_area = ctk.CTkFrame(root, fg_color=_C["bg"])
        content_area.pack(side="left", fill="both", expand=True)

        for name, icon, PanelClass in self._NAV:
            panel = PanelClass(content_area, self._status)
            self._panels[name] = panel

            btn = _NavBtn(sidebar, label=name, icon=icon,
                          on_click=lambda n=name: self._switch(n))
            btn.pack(fill="x", padx=12, pady=(8, 0))
            self._nav_btns.append(btn)

        # Alt dolgu + versiyon
        ctk.CTkFrame(sidebar, fg_color="transparent").pack(
            fill="y", expand=True)
        ctk.CTkLabel(sidebar, text="v1.1.0",
                     font=ctk.CTkFont(size=10),
                     text_color=_C["dim"]).pack(pady=12)

        self._switch("Veri Analizi")

    def _switch(self, name: str) -> None:
        for panel in self._panels.values():
            panel.pack_forget()
        self._panels[name].pack(fill="both", expand=True)

        nav_names = [n for n, _, _ in self._NAV]
        for i, btn in enumerate(self._nav_btns):
            btn.activate(nav_names[i] == name)


# ─────────────────────────────────────────────────────────────────────── #
#  Giriş noktası                                                            #
# ─────────────────────────────────────────────────────────────────────── #

def launch() -> None:
    """Grafiksel uygulamayı başlatır (main.py'den çağrılır)."""
    App().mainloop()


if __name__ == "__main__":
    launch()
