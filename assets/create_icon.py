"""
assets/create_icon.py
~~~~~~~~~~~~~~~~~~~~~
Pillow ile yt-manual-analyzer ikonu oluşturur.
Çalıştır: python assets/create_icon.py
Çıktı   : assets/icon.ico  (16 / 32 / 48 / 64 / 128 / 256 px)
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ── Renk paleti (uygulama temasıyla aynı) ────────────────────────────── #
BG_DARK   = (26,  26,  46)       # #1a1a2e  — dış arka plan
CARD_DARK = (15,  52,  96)       # #0f3460  — kart arka planı
ACCENT_1  = (31, 106, 165)       # #1f6aa5  — birincil mavi
ACCENT_2  = (45, 127, 193)       # #2d7fc1  — açık mavi
WHITE     = (255, 255, 255)
WHITE_A80 = (255, 255, 255, 204) # %80 alfa

SIZES = [256, 128, 64, 48, 32, 16]


def _rounded_rect(draw: ImageDraw.Draw,
                  xy: tuple, radius: int,
                  fill: tuple, outline: tuple | None = None,
                  outline_width: int = 2) -> None:
    """Anti-alias için 4× büyütülmüş yuvarlak dikdörtgen çizer."""
    x0, y0, x1, y1 = xy
    r = radius
    draw.rounded_rectangle([x0, y0, x1, y1], radius=r, fill=fill,
                            outline=outline, width=outline_width)


def _draw_play_triangle(draw: ImageDraw.Draw,
                        cx: float, cy: float, size: float,
                        color: tuple) -> None:
    """Merkezi (cx, cy) olan eşkenar üçgen (oynat ikonu)."""
    h = size * math.sqrt(3) / 2
    pts = [
        (cx - size * 0.15, cy - h * 0.5),
        (cx - size * 0.15, cy + h * 0.5),
        (cx + size * 0.85, cy),
    ]
    draw.polygon(pts, fill=color)


def _draw_gradient_rect(img: Image.Image,
                        x0: int, y0: int, x1: int, y1: int,
                        color_top: tuple, color_bot: tuple,
                        radius: int) -> Image.Image:
    """Dikey gradient + yuvarlak köşe (RGBA)."""
    w, h = x1 - x0, y1 - y0
    grad = Image.new("RGBA", (w, h))
    for y in range(h):
        t = y / max(h - 1, 1)
        r = int(color_top[0] + (color_bot[0] - color_top[0]) * t)
        g = int(color_top[1] + (color_bot[1] - color_top[1]) * t)
        b = int(color_top[2] + (color_bot[2] - color_top[2]) * t)
        for x in range(w):
            grad.putpixel((x, y), (r, g, b, 255))

    # Yuvarlak maske
    mask = Image.new("L", (w, h), 0)
    m_draw = ImageDraw.Draw(mask)
    m_draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
    grad.putalpha(mask)

    img.paste(grad, (x0, y0), grad)
    return img


def render(size: int) -> Image.Image:
    """Verilen piksel boyutunda ikon üretir (RGBA)."""
    S = size
    scale = S / 256           # 256 px referans boyutu

    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # ── Arka plan dairesi ────────────────────────────────────────────── #
    bg = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    bg_draw = ImageDraw.Draw(bg)
    margin = int(4 * scale)
    bg_draw.ellipse([margin, margin, S - margin, S - margin],
                    fill=(*BG_DARK, 255))
    img.paste(bg, (0, 0), bg)

    # ── Gradient kart (yuvarlak dikdörtgen) ─────────────────────────── #
    cx, cy = S // 2, S // 2
    card_w = int(200 * scale)
    card_h = int(140 * scale)
    cx0, cy0 = cx - card_w // 2, cy - card_h // 2
    radius  = int(28 * scale)
    img = _draw_gradient_rect(img, cx0, cy0, cx0 + card_w, cy0 + card_h,
                              ACCENT_1, ACCENT_2, radius)

    # ── İnce beyaz kenarlık ──────────────────────────────────────────── #
    border_layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    b_draw = ImageDraw.Draw(border_layer)
    bw = max(1, int(2 * scale))
    b_draw.rounded_rectangle(
        [cx0, cy0, cx0 + card_w, cy0 + card_h],
        radius=radius, outline=(255, 255, 255, 60), width=bw,
    )
    img.alpha_composite(border_layer)

    # ── Oynat üçgeni ─────────────────────────────────────────────────── #
    tri_layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    t_draw    = ImageDraw.Draw(tri_layer)
    if S >= 48:
        tri_size = int(70 * scale)
        tri_cx   = cx - int(4 * scale)     # hafif sola kaydır
        tri_cy   = cy - int(14 * scale)    # hafif yukarı
        _draw_play_triangle(t_draw, tri_cx, tri_cy, tri_size, WHITE_A80)
    img.alpha_composite(tri_layer)

    # ── Alt etiket: "MA" harfleri (≥ 48 px) ─────────────────────────── #
    if S >= 48:
        lbl_layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        l_draw    = ImageDraw.Draw(lbl_layer)
        font_size = max(8, int(28 * scale))
        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                font_size,
            )
        except OSError:
            font = ImageFont.load_default()

        text = "yt-MA"
        if S < 80:
            text = "MA"
        bbox  = l_draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        tx = cx - tw // 2
        ty = cy0 + card_h - th - int(12 * scale)
        l_draw.text((tx, ty), text, font=font, fill=(255, 255, 255, 200))
        img.alpha_composite(lbl_layer)

    # ── Hafif gölge / parlaklık ──────────────────────────────────────── #
    if S >= 64:
        glow = img.filter(ImageFilter.GaussianBlur(radius=int(2 * scale)))
        img = Image.alpha_composite(glow, img)

    return img


def build_ico(output: Path = Path("assets/icon.ico")) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)

    images: list[Image.Image] = []
    for s in SIZES:
        frame = render(s).convert("RGBA")
        images.append(frame)

    # .ico: en büyük boyutu kaydederken diğerlerini sizes listesine ekle
    images[0].save(
        output,
        format="ICO",
        sizes=[(s, s) for s in SIZES],
        append_images=images[1:],
    )
    print(f"  ✓  İkon oluşturuldu → {output}  "
          f"({', '.join(str(s) for s in SIZES)} px)")


def build_png(output: Path = Path("assets/icon.png"), size: int = 256) -> None:
    """macOS / Linux için PNG ikon."""
    output.parent.mkdir(parents=True, exist_ok=True)
    render(size).save(output, format="PNG")
    print(f"  ✓  PNG ikon → {output}  ({size}×{size} px)")


if __name__ == "__main__":
    build_ico()
    build_png()
