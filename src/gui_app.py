"""
src/gui_app.py  —  yt-manual-analyzer grafiksel arayüzü

Başlatmak için:
    python -m src.gui_app
    # veya doğrudan:
    python src/gui_app.py

Gereksinim:
    pip install customtkinter
"""

from __future__ import annotations

import os
import queue
import threading
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from dotenv import load_dotenv

load_dotenv()

# ── Tema ─────────────────────────────────────────────────────────────────── #
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

_ACCENT   = "#1f6aa5"
_SIDEBAR_W = 200
_PAD      = 16

# ── Renk paleti ──────────────────────────────────────────────────────────── #
_C = {
    "bg":          "#1a1a2e",   # ana arka plan
    "sidebar":     "#16213e",   # sol panel
    "card":        "#0f3460",   # kart / iç panel
    "btn_active":  "#1f6aa5",
    "btn_hover":   "#2d7fc1",
    "btn_idle":    "#1e2a3a",
    "text":        "#e0e0e0",
    "text_dim":    "#8899aa",
    "success":     "#2ecc71",
    "warning":     "#f39c12",
    "error":       "#e74c3c",
    "border":      "#1f3a5f",
}

# ─────────────────────────────────────────────────────────────────────────── #
#  Yardımcılar                                                                #
# ─────────────────────────────────────────────────────────────────────────── #

def _thread(fn, *args, **kwargs):
    """Fonksiyonu daemon thread üzerinde çalıştırır."""
    t = threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True)
    t.start()
    return t


# ─────────────────────────────────────────────────────────────────────────── #
#  Sidebar butonu                                                              #
# ─────────────────────────────────────────────────────────────────────────── #

class _SidebarBtn(ctk.CTkButton):
    def __init__(self, master, text: str, icon: str, command, **kw):
        super().__init__(
            master,
            text=f"  {icon}  {text}",
            command=command,
            anchor="w",
            height=44,
            corner_radius=8,
            border_width=0,
            fg_color=_C["btn_idle"],
            hover_color=_C["btn_hover"],
            text_color=_C["text"],
            font=ctk.CTkFont(size=14),
            **kw,
        )

    def set_active(self, active: bool):
        self.configure(fg_color=_C["btn_active"] if active else _C["btn_idle"])


# ─────────────────────────────────────────────────────────────────────────── #
#  Sekmeler (paneller)                                                         #
# ─────────────────────────────────────────────────────────────────────────── #

class _BasePanel(ctk.CTkFrame):
    """Tüm içerik panellerinin ortak tabanı."""

    def __init__(self, master, **kw):
        super().__init__(master, fg_color=_C["bg"], **kw)

    # Alt sınıflar çağırır —————————————————————
    def _section_label(self, text: str) -> ctk.CTkLabel:
        lbl = ctk.CTkLabel(
            self, text=text,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=_C["text_dim"],
        )
        return lbl

    def _output_box(self, parent=None) -> ctk.CTkTextbox:
        box = ctk.CTkTextbox(
            parent or self,
            font=ctk.CTkFont(family="Courier", size=12),
            text_color=_C["text"],
            fg_color=_C["card"],
            border_color=_C["border"],
            border_width=1,
            wrap="word",
            state="disabled",
        )
        return box

    def _write(self, box: ctk.CTkTextbox, text: str, clear: bool = False):
        """Thread-safe textbox yazımı."""
        box.configure(state="normal")
        if clear:
            box.delete("0.0", "end")
        box.insert("end", text)
        box.see("end")
        box.configure(state="disabled")

    def _clear(self, box: ctk.CTkTextbox):
        box.configure(state="normal")
        box.delete("0.0", "end")
        box.configure(state="disabled")


# ── VERİ ANALİZİ PANELİ ─────────────────────────────────────────────────── #

class DataAnalysisPanel(_BasePanel):
    def __init__(self, master, status_bar, **kw):
        super().__init__(master, **kw)
        self._status = status_bar
        self._file: Path | None = None
        self._q: queue.Queue = queue.Queue()
        self._build()
        self._poll()

    def _build(self):
        # ── Başlık ──────────────────────────────────────────────────── #
        ctk.CTkLabel(
            self,
            text="Veri Analizi",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=_C["text"],
        ).pack(anchor="w", padx=_PAD, pady=(_PAD, 4))

        ctk.CTkLabel(
            self,
            text="YouTube Studio'dan indirdiğiniz CSV/Excel dosyasını seçin.",
            font=ctk.CTkFont(size=12),
            text_color=_C["text_dim"],
        ).pack(anchor="w", padx=_PAD, pady=(0, _PAD))

        # ── Dosya seçimi ─────────────────────────────────────────────── #
        file_row = ctk.CTkFrame(self, fg_color="transparent")
        file_row.pack(fill="x", padx=_PAD, pady=(0, 8))

        self._file_label = ctk.CTkLabel(
            file_row,
            text="Dosya seçilmedi",
            font=ctk.CTkFont(size=12),
            text_color=_C["text_dim"],
            anchor="w",
        )
        self._file_label.pack(side="left", fill="x", expand=True)

        ctk.CTkButton(
            file_row,
            text="📂  Gözat",
            width=110,
            height=36,
            fg_color=_C["btn_active"],
            hover_color=_C["btn_hover"],
            font=ctk.CTkFont(size=13),
            command=self._browse,
        ).pack(side="right", padx=(8, 0))

        # ── Klasör analizi ───────────────────────────────────────────── #
        folder_row = ctk.CTkFrame(self, fg_color="transparent")
        folder_row.pack(fill="x", padx=_PAD, pady=(0, _PAD))

        self._folder_label = ctk.CTkLabel(
            folder_row,
            text="veya Analizler/ klasöründeki tüm dosyaları analiz et:",
            font=ctk.CTkFont(size=12),
            text_color=_C["text_dim"],
            anchor="w",
        )
        self._folder_label.pack(side="left", fill="x", expand=True)

        ctk.CTkButton(
            folder_row,
            text="📁  Klasörü Analiz Et",
            width=160,
            height=36,
            fg_color=_C["btn_idle"],
            hover_color=_C["btn_hover"],
            font=ctk.CTkFont(size=13),
            command=self._analyze_folder,
        ).pack(side="right", padx=(8, 0))

        # ── Analiz butonu ────────────────────────────────────────────── #
        self._analyze_btn = ctk.CTkButton(
            self,
            text="▶  Dosyayı Analiz Et",
            height=42,
            fg_color=_C["btn_active"],
            hover_color=_C["btn_hover"],
            font=ctk.CTkFont(size=14, weight="bold"),
            state="disabled",
            command=self._run_analysis,
        )
        self._analyze_btn.pack(fill="x", padx=_PAD, pady=(0, _PAD))

        # ── İlerleme barı ────────────────────────────────────────────── #
        self._progress = ctk.CTkProgressBar(
            self, mode="indeterminate",
            progress_color=_C["btn_active"],
            fg_color=_C["card"],
            height=6,
        )
        self._progress.pack(fill="x", padx=_PAD, pady=(0, _PAD))
        self._progress.set(0)

        # ── Sonuç kutusu ─────────────────────────────────────────────── #
        self._section_label("Analiz Sonuçları").pack(anchor="w", padx=_PAD, pady=(4, 4))

        self._out = self._output_box()
        self._out.pack(fill="both", expand=True, padx=_PAD, pady=(0, _PAD))

    # ── Olaylar ──────────────────────────────────────────────────────── #

    def _browse(self):
        path = filedialog.askopenfilename(
            title="CSV / Excel dosyası seçin",
            filetypes=[
                ("Desteklenen dosyalar", "*.csv *.xlsx *.xls"),
                ("CSV", "*.csv"),
                ("Excel", "*.xlsx *.xls"),
                ("Tüm dosyalar", "*.*"),
            ],
        )
        if path:
            self._file = Path(path)
            self._file_label.configure(
                text=self._file.name, text_color=_C["text"]
            )
            self._analyze_btn.configure(state="normal")

    def _analyze_folder(self):
        self._file = None
        self._file_label.configure(
            text="Analizler/ klasöründeki tüm dosyalar",
            text_color=_C["text"],
        )
        self._analyze_btn.configure(state="normal", text="▶  Klasörü Analiz Et")
        # Hemen çalıştır
        self._run_analysis(folder_mode=True)

    def _run_analysis(self, folder_mode: bool = False):
        self._analyze_btn.configure(state="disabled")
        self._progress.configure(mode="indeterminate")
        self._progress.start()
        self._status.set("Dosyalar okunuyor ve analiz ediliyor…", "info")
        self._clear(self._out)
        _thread(self._worker, folder_mode=folder_mode)

    def _worker(self, folder_mode: bool = False):
        try:
            from src.file_processor import FileProcessor
            from src.ai_strategist import AIStrategist, QueryType

            fp = FileProcessor()
            self._q.put(("log", "📂  Dosyalar okunuyor…\n"))

            if folder_mode or self._file is None:
                merged = fp.load_folder("Analizler")
            else:
                report = fp.load_file(self._file)
                merged = fp.merge([report])

            if merged.warnings:
                for w in merged.warnings:
                    self._q.put(("log", f"⚠  {w}\n"))

            if merged.stats is None:
                self._q.put(("error", "Geçerli veri bulunamadı. Sütun adlarını kontrol edin."))
                return

            s = merged.stats
            summary = (
                f"{'─'*50}\n"
                f"  Analiz Raporu  ({len(merged.files)} dosya)\n"
                f"{'─'*50}\n"
                f"  Toplam video          : {s.total_videos}\n"
                f"  Toplam izlenme        : {s.total_views:,}\n"
                f"  Toplam izleme süresi  : {s.total_watch_time_hours:,.1f} saat\n"
                f"  Ortalama izlenme      : {s.avg_views:,.0f}\n"
                f"  Medyan izlenme        : {s.median_views:,.0f}\n"
                f"  Ortalama CTR          : %{s.avg_ctr_pct}\n"
                f"  Ortalama izleme süresi: {s.avg_watch_fmt}\n"
                f"  Toplam abone değişimi : {s.total_subscribers_gained:+,}\n"
                f"{'─'*50}\n\n"
            )

            if s.top_performers:
                summary += "  En Çok İzlenen Videolar:\n"
                for i, v in enumerate(s.top_performers, 1):
                    summary += f"  {i}. {v.title[:55]}\n"
                    summary += f"     {v.views:,} izlenme  ·  CTR %{v.ctr_pct}  ·  {v.avg_watch_fmt}\n"
                summary += "\n"

            if s.underperformers:
                summary += "  Gelişim Fırsatları:\n"
                for i, v in enumerate(s.underperformers, 1):
                    summary += f"  {i}. {v.title[:55]}\n"
                    summary += f"     {v.views:,} izlenme  ·  CTR %{v.ctr_pct}\n"
                summary += "\n"

            self._q.put(("log", summary))
            self._q.put(("log", "🤖  AI strateji analizi yapılıyor…\n"))

            strategist = AIStrategist()
            report = strategist.analyze(merged, query=QueryType.CONTENT_FOCUS)

            ai_block = f"{'─'*50}\n  AI Strateji İçgörüleri\n{'─'*50}\n"
            if report.strengths:
                ai_block += "\n  Güçlü Yönler:\n"
                for s_ in report.strengths[:5]:
                    ai_block += f"  ✓ {s_}\n"
            if report.primary_recommendation:
                ai_block += f"\n  Birincil Öneri:\n  → {report.primary_recommendation}\n"
            if report.action_items:
                ai_block += "\n  Bu Hafta Yapılacaklar:\n"
                for i, item in enumerate(report.action_items[:5], 1):
                    ai_block += f"  {i}. {item}\n"
            ai_block += "\n"

            self._q.put(("log", ai_block))
            self._q.put(("done", None))

        except FileNotFoundError as exc:
            self._q.put(("error", str(exc)))
        except Exception:
            self._q.put(("error", traceback.format_exc()))

    def _poll(self):
        """GUI thread'inde queue'yu 100ms aralıklarla kontrol eder."""
        try:
            while True:
                kind, data = self._q.get_nowait()
                if kind == "log":
                    self._write(self._out, data)
                elif kind == "done":
                    self._progress.stop()
                    self._progress.set(1)
                    self._analyze_btn.configure(state="normal")
                    self._status.set("Analiz tamamlandı ✓", "success")
                elif kind == "error":
                    self._write(self._out, f"\n❌  HATA:\n{data}\n")
                    self._progress.stop()
                    self._progress.set(0)
                    self._analyze_btn.configure(state="normal")
                    self._status.set("Hata oluştu", "error")
        except queue.Empty:
            pass
        self.after(100, self._poll)


# ── SENARYO YAZARI PANELİ ────────────────────────────────────────────────── #

class ScriptWriterPanel(_BasePanel):
    def __init__(self, master, status_bar, **kw):
        super().__init__(master, **kw)
        self._status = status_bar
        self._q: queue.Queue = queue.Queue()
        self._build()
        self._poll()

    def _build(self):
        # ── Başlık ──────────────────────────────────────────────────── #
        ctk.CTkLabel(
            self,
            text="Senaryo Yazarı",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=_C["text"],
        ).pack(anchor="w", padx=_PAD, pady=(_PAD, 4))

        ctk.CTkLabel(
            self,
            text="4 aşamalı pipeline: taslak → iddia çıkarımı → çapraz doğrulama → düzeltme",
            font=ctk.CTkFont(size=12),
            text_color=_C["text_dim"],
        ).pack(anchor="w", padx=_PAD, pady=(0, _PAD))

        # ── Giriş alanları ───────────────────────────────────────────── #
        card = ctk.CTkFrame(self, fg_color=_C["card"], corner_radius=10, border_width=1,
                            border_color=_C["border"])
        card.pack(fill="x", padx=_PAD, pady=(0, _PAD))

        self._section_label("Konu Başlığı *").pack(anchor="w", in_=card, padx=12, pady=(12, 4))
        self._topic_entry = ctk.CTkEntry(
            card,
            placeholder_text="Örn: Kuantum bilgisayarların geleceği",
            height=40,
            font=ctk.CTkFont(size=13),
            fg_color="#0a2540",
            border_color=_C["border"],
        )
        self._topic_entry.pack(fill="x", padx=12, pady=(0, 8))

        self._section_label("Video Başlığı (boş bırakılırsa konu başlığı kullanılır)").pack(
            anchor="w", in_=card, padx=12, pady=(4, 4)
        )
        self._title_entry = ctk.CTkEntry(
            card,
            placeholder_text="Örn: Kuantum Bilgisayarlar Hayatımızı Nasıl Değiştirecek?",
            height=40,
            font=ctk.CTkFont(size=13),
            fg_color="#0a2540",
            border_color=_C["border"],
        )
        self._title_entry.pack(fill="x", padx=12, pady=(0, 8))

        self._section_label("Ek Yönergeler (isteğe bağlı)").pack(
            anchor="w", in_=card, padx=12, pady=(4, 4)
        )
        self._instr_box = ctk.CTkTextbox(
            card, height=70, font=ctk.CTkFont(size=12),
            fg_color="#0a2540", border_color=_C["border"], border_width=1,
        )
        self._instr_box.pack(fill="x", padx=12, pady=(0, 12))

        # ── Oluştur butonu ───────────────────────────────────────────── #
        self._gen_btn = ctk.CTkButton(
            self,
            text="✍  12.000 Karakterlik Senaryo Oluştur",
            height=48,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color=_C["btn_active"],
            hover_color=_C["btn_hover"],
            command=self._run,
        )
        self._gen_btn.pack(fill="x", padx=_PAD, pady=(0, 8))

        # ── İlerleme barı ────────────────────────────────────────────── #
        prog_row = ctk.CTkFrame(self, fg_color="transparent")
        prog_row.pack(fill="x", padx=_PAD, pady=(0, 4))

        self._progress = ctk.CTkProgressBar(
            prog_row, mode="indeterminate",
            progress_color=_C["btn_active"],
            fg_color=_C["card"],
            height=8,
        )
        self._progress.pack(side="left", fill="x", expand=True)
        self._progress.set(0)

        self._prog_label = ctk.CTkLabel(
            prog_row, text="", width=140,
            font=ctk.CTkFont(size=11), text_color=_C["text_dim"],
        )
        self._prog_label.pack(side="right", padx=(8, 0))

        # ── Adım göstergesi ──────────────────────────────────────────── #
        self._step_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._step_frame.pack(fill="x", padx=_PAD, pady=(0, 8))
        self._step_labels: list[ctk.CTkLabel] = []
        steps = ["1. Taslak", "2. İddialar", "3. Doğrulama", "4. Düzeltme"]
        for step in steps:
            lbl = ctk.CTkLabel(
                self._step_frame, text=step,
                font=ctk.CTkFont(size=11),
                text_color=_C["text_dim"],
                fg_color=_C["card"],
                corner_radius=6,
                padx=8, pady=4,
            )
            lbl.pack(side="left", padx=(0, 6))
            self._step_labels.append(lbl)

        # ── Senaryo çıkışı ───────────────────────────────────────────── #
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=_PAD, pady=(0, 4))

        self._section_label("Üretilen Senaryo").pack(side="left", in_=btn_row)
        ctk.CTkButton(
            btn_row,
            text="📋  Kopyala",
            width=100, height=28,
            fg_color=_C["btn_idle"],
            hover_color=_C["btn_hover"],
            font=ctk.CTkFont(size=12),
            command=self._copy_output,
        ).pack(side="right")
        ctk.CTkButton(
            btn_row,
            text="💾  Kaydet",
            width=100, height=28,
            fg_color=_C["btn_idle"],
            hover_color=_C["btn_hover"],
            font=ctk.CTkFont(size=12),
            command=self._save_output,
        ).pack(side="right", padx=(0, 6))

        self._out = self._output_box()
        self._out.pack(fill="both", expand=True, padx=_PAD, pady=(0, _PAD))

    # ── Adım renklendirme ─────────────────────────────────────────────────

    def _set_step(self, idx: int):
        """idx. adımı vurgular, öncekini tamamlandı olarak işaretler."""
        for i, lbl in enumerate(self._step_labels):
            if i < idx:
                lbl.configure(text_color=_C["success"], fg_color=_C["card"])
            elif i == idx:
                lbl.configure(text_color="#ffffff", fg_color=_C["btn_active"])
            else:
                lbl.configure(text_color=_C["text_dim"], fg_color=_C["card"])

    # ── Buton eylemleri ───────────────────────────────────────────────────

    def _copy_output(self):
        content = self._out.get("0.0", "end").strip()
        if content:
            self.clipboard_clear()
            self.clipboard_append(content)
            self._status.set("Senaryo panoya kopyalandı ✓", "success")

    def _save_output(self):
        content = self._out.get("0.0", "end").strip()
        if not content:
            messagebox.showwarning("Boş İçerik", "Kaydetmek için önce senaryo oluşturun.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".md",
            filetypes=[("Markdown", "*.md"), ("Text", "*.txt"), ("Tüm dosyalar", "*.*")],
            initialfile="senaryo.md",
        )
        if path:
            Path(path).write_text(content, encoding="utf-8")
            self._status.set(f"Kaydedildi → {Path(path).name}", "success")

    def _run(self):
        topic = self._topic_entry.get().strip()
        if not topic:
            messagebox.showwarning("Eksik Bilgi", "Lütfen konu başlığını girin.")
            return

        title = self._title_entry.get().strip() or topic
        instr = self._instr_box.get("0.0", "end").strip()

        self._gen_btn.configure(state="disabled")
        self._progress.configure(mode="indeterminate")
        self._progress.start()
        for lbl in self._step_labels:
            lbl.configure(text_color=_C["text_dim"], fg_color=_C["card"])
        self._clear(self._out)
        self._status.set("Senaryo üretiliyor… (2-4 dakika)", "info")

        _thread(self._worker, topic=topic, title=title, instr=instr)

    def _worker(self, topic: str, title: str, instr: str):
        try:
            from src.writer_engine import WriterEngine

            engine = WriterEngine()

            # Adım ilerleme bildirimleri
            original_pass_draft     = engine._pass_draft
            original_pass_extract   = engine._pass_extract_claims
            original_pass_verify    = engine._pass_verify
            original_pass_correct   = engine._pass_correct

            def _wrap_draft(*a, **kw):
                self._q.put(("step", 0))
                return original_pass_draft(*a, **kw)

            def _wrap_extract(*a, **kw):
                self._q.put(("step", 1))
                return original_pass_extract(*a, **kw)

            def _wrap_verify(*a, **kw):
                self._q.put(("step", 2))
                return original_pass_verify(*a, **kw)

            def _wrap_correct(*a, **kw):
                self._q.put(("step", 3))
                return original_pass_correct(*a, **kw)

            engine._pass_draft           = _wrap_draft
            engine._pass_extract_claims  = _wrap_extract
            engine._pass_verify          = _wrap_verify
            engine._pass_correct         = _wrap_correct

            script = engine.write(
                source=topic, title=title,
                extra_instructions=instr,
                auto_save=True,
            )

            self._q.put(("result", script))

        except Exception:
            self._q.put(("error", traceback.format_exc()))

    def _poll(self):
        try:
            while True:
                kind, data = self._q.get_nowait()
                if kind == "step":
                    self._set_step(data)
                    labels = ["Taslak yazılıyor…", "İddialar çıkarılıyor…",
                              "Çapraz doğrulanıyor…", "Düzeltmeler uygulanıyor…"]
                    self._prog_label.configure(text=labels[data])
                    self._status.set(labels[data], "info")

                elif kind == "result":
                    script = data
                    self._progress.stop()
                    self._progress.set(1)
                    self._gen_btn.configure(state="normal")
                    for lbl in self._step_labels:
                        lbl.configure(text_color=_C["success"], fg_color=_C["card"])
                    self._prog_label.configure(text="Tamamlandı ✓")

                    len_ok  = script.meets_length
                    ver_ok  = script.verification.is_clean
                    summary = (
                        f"{'─'*60}\n"
                        f"  {'✓' if len_ok else '⚠'} {script.char_count:,} karakter  ·  "
                        f"{script.word_count:,} kelime  ·  {script.passes_completed} pas\n"
                        f"  {'✓' if ver_ok else '⚠'} Doğruluk skoru: "
                        f"{script.verification.score}/100  ·  "
                        f"{script.verification.claims_extracted} iddia incelendi\n"
                    )
                    if script.domains:
                        summary += f"  Alan(lar): {', '.join(script.domains)}\n"
                    if script.saved_path:
                        summary += f"  Kaydedildi → {script.saved_path}\n"
                    summary += f"{'─'*60}\n\n"

                    self._write(self._out, summary + script.content, clear=True)
                    self._status.set(
                        f"Senaryo tamamlandı ✓  —  {script.char_count:,} karakter", "success"
                    )

                elif kind == "error":
                    self._progress.stop()
                    self._progress.set(0)
                    self._gen_btn.configure(state="normal")
                    self._prog_label.configure(text="Hata")
                    self._write(self._out, f"\n❌  HATA:\n{data}\n")
                    self._status.set("Hata oluştu", "error")

        except queue.Empty:
            pass
        self.after(100, self._poll)


# ── AYARLAR PANELİ ───────────────────────────────────────────────────────── #

class SettingsPanel(_BasePanel):
    def __init__(self, master, status_bar, **kw):
        super().__init__(master, **kw)
        self._status = status_bar
        self._build()

    def _build(self):
        ctk.CTkLabel(
            self,
            text="Ayarlar",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=_C["text"],
        ).pack(anchor="w", padx=_PAD, pady=(_PAD, 4))

        ctk.CTkLabel(
            self,
            text="API anahtarları ve model tercihlerini buradan yapılandırın.",
            font=ctk.CTkFont(size=12),
            text_color=_C["text_dim"],
        ).pack(anchor="w", padx=_PAD, pady=(0, _PAD))

        card = ctk.CTkFrame(self, fg_color=_C["card"], corner_radius=10,
                            border_width=1, border_color=_C["border"])
        card.pack(fill="x", padx=_PAD, pady=(0, _PAD))

        fields = [
            ("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY", True),
            ("YOUTUBE_API_KEY (isteğe bağlı)", "YOUTUBE_API_KEY", False),
            ("Claude Modeli", "CLAUDE_MODEL", False),
            ("Maks. Token (genel)", "MAX_TOKENS", False),
            ("Maks. Token (senaryo)", "SCENARIO_MAX_TOKENS", False),
        ]

        self._entries: dict[str, ctk.CTkEntry] = {}

        for label, env_key, required in fields:
            ctk.CTkLabel(
                card,
                text=label + (" *" if required else ""),
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=_C["text_dim"],
            ).pack(anchor="w", padx=12, pady=(10, 2))

            entry = ctk.CTkEntry(
                card,
                height=38,
                font=ctk.CTkFont(size=12),
                fg_color="#0a2540",
                border_color=_C["border"],
                show="*" if "KEY" in env_key else "",
            )
            current_val = os.getenv(env_key, "")
            if current_val:
                entry.insert(0, current_val)
            entry.pack(fill="x", padx=12, pady=(0, 4))
            self._entries[env_key] = entry

        ctk.CTkButton(
            card,
            text="💾  .env Dosyasına Kaydet",
            height=40,
            fg_color=_C["btn_active"],
            hover_color=_C["btn_hover"],
            font=ctk.CTkFont(size=13),
            command=self._save_env,
        ).pack(fill="x", padx=12, pady=(8, 12))

        # ── Model bilgisi ────────────────────────────────────────────── #
        info_card = ctk.CTkFrame(self, fg_color=_C["card"], corner_radius=10,
                                 border_width=1, border_color=_C["border"])
        info_card.pack(fill="x", padx=_PAD, pady=(0, _PAD))

        ctk.CTkLabel(
            info_card,
            text="Kullanılabilir Modeller",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=_C["text"],
        ).pack(anchor="w", padx=12, pady=(12, 4))

        models = [
            ("claude-sonnet-4-6", "Önerilen — hız/kalite dengesi"),
            ("claude-opus-4-7",   "Maksimum kalite — daha yavaş"),
            ("claude-haiku-4-5-20251001",  "En hızlı — kısa içerikler için"),
        ]
        for model, desc in models:
            row = ctk.CTkFrame(info_card, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=2)
            ctk.CTkLabel(
                row, text=model,
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=_C["text"],
            ).pack(side="left")
            ctk.CTkLabel(
                row, text=f"  —  {desc}",
                font=ctk.CTkFont(size=12),
                text_color=_C["text_dim"],
            ).pack(side="left")
        ctk.CTkLabel(info_card, text="").pack(pady=4)

    def _save_env(self):
        env_path = Path(".env")
        lines: list[str] = []

        if env_path.exists():
            existing = env_path.read_text(encoding="utf-8").splitlines()
            existing_keys = set()
            for line in existing:
                key = line.split("=", 1)[0].strip()
                if key in self._entries:
                    val = self._entries[key].get().strip()
                    if val:
                        lines.append(f"{key}={val}")
                    existing_keys.add(key)
                else:
                    lines.append(line)
            for key, entry in self._entries.items():
                if key not in existing_keys:
                    val = entry.get().strip()
                    if val:
                        lines.append(f"{key}={val}")
        else:
            for key, entry in self._entries.items():
                val = entry.get().strip()
                if val:
                    lines.append(f"{key}={val}")

        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # Ortam değişkenlerini güncelle
        for key, entry in self._entries.items():
            val = entry.get().strip()
            if val:
                os.environ[key] = val

        load_dotenv(override=True)
        self._status.set(".env dosyası güncellendi ✓", "success")


# ── DURUM ÇUBUĞU ─────────────────────────────────────────────────────────── #

class _StatusBar(ctk.CTkFrame):
    _COLORS = {
        "info":    _C["text_dim"],
        "success": _C["success"],
        "warning": _C["warning"],
        "error":   _C["error"],
    }

    def __init__(self, master, **kw):
        super().__init__(master, fg_color=_C["sidebar"], height=30, corner_radius=0, **kw)
        self._lbl = ctk.CTkLabel(
            self, text="Hazır",
            font=ctk.CTkFont(size=11),
            text_color=_C["text_dim"],
        )
        self._lbl.pack(side="left", padx=12, pady=4)

    def set(self, text: str, kind: str = "info"):
        self._lbl.configure(text=text, text_color=self._COLORS.get(kind, _C["text_dim"]))


# ─────────────────────────────────────────────────────────────────────────── #
#  Ana Uygulama                                                                #
# ─────────────────────────────────────────────────────────────────────────── #

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("yt-manual-analyzer")
        self.geometry("1100x720")
        self.minsize(900, 600)
        self.configure(fg_color=_C["bg"])

        self._build()

    def _build(self):
        # ── Durum çubuğu (altta) ────────────────────────────────────── #
        self._status_bar = _StatusBar(self)
        self._status_bar.pack(side="bottom", fill="x")

        # ── Ana layout ──────────────────────────────────────────────── #
        container = ctk.CTkFrame(self, fg_color=_C["bg"])
        container.pack(fill="both", expand=True)

        # ── Sidebar ─────────────────────────────────────────────────── #
        sidebar = ctk.CTkFrame(
            container,
            width=_SIDEBAR_W,
            fg_color=_C["sidebar"],
            corner_radius=0,
        )
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        # Logo
        ctk.CTkLabel(
            sidebar,
            text="yt-manual\nanalyzer",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=_C["text"],
            justify="left",
        ).pack(anchor="w", padx=16, pady=(20, 4))

        ctk.CTkLabel(
            sidebar,
            text="Manuel Analiz  ×  Claude API",
            font=ctk.CTkFont(size=10),
            text_color=_C["text_dim"],
        ).pack(anchor="w", padx=16, pady=(0, 20))

        ctk.CTkFrame(sidebar, height=1, fg_color=_C["border"]).pack(fill="x", padx=12)

        # Menü butonları
        nav_items = [
            ("Veri Analizi",   "📊"),
            ("Senaryo Yazarı", "✍"),
            ("Ayarlar",        "⚙"),
        ]
        self._nav_btns: list[_SidebarBtn] = []
        for name, icon in nav_items:
            btn = _SidebarBtn(
                sidebar, text=name, icon=icon,
                command=lambda n=name: self._switch(n),
            )
            btn.pack(fill="x", padx=12, pady=(8, 0))
            self._nav_btns.append(btn)

        # Alt boşluk + versiyon
        ctk.CTkFrame(sidebar, fg_color="transparent").pack(fill="y", expand=True)
        ctk.CTkLabel(
            sidebar,
            text="v1.0.0",
            font=ctk.CTkFont(size=10),
            text_color=_C["text_dim"],
        ).pack(pady=12)

        # ── İçerik alanı ────────────────────────────────────────────── #
        self._content = ctk.CTkFrame(container, fg_color=_C["bg"])
        self._content.pack(side="left", fill="both", expand=True)

        self._panels: dict[str, _BasePanel] = {
            "Veri Analizi":   DataAnalysisPanel(self._content, self._status_bar),
            "Senaryo Yazarı": ScriptWriterPanel(self._content, self._status_bar),
            "Ayarlar":        SettingsPanel(self._content, self._status_bar),
        }

        self._switch("Veri Analizi")

    def _switch(self, name: str):
        for panel in self._panels.values():
            panel.pack_forget()
        self._panels[name].pack(fill="both", expand=True)

        for i, btn in enumerate(self._nav_btns):
            labels = ["Veri Analizi", "Senaryo Yazarı", "Ayarlar"]
            btn.set_active(labels[i] == name)


# ─────────────────────────────────────────────────────────────────────────── #
#  Giriş noktası                                                               #
# ─────────────────────────────────────────────────────────────────────────── #

def launch():
    """GUI uygulamasını başlatır."""
    app = App()
    app.mainloop()


if __name__ == "__main__":
    launch()
