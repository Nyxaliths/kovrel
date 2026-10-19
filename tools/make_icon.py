"""Draws the Kovrel logo and writes the app icon + web icons.

Run:  python tools/make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
S = 1024  # draw big, scale down for crisp edges

TILE = (23, 26, 31)            # matches the app's card colour
MINT_TOP, MINT_BOTTOM = (110, 231, 197), (47, 214, 163)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def stroke(d, a, b, width, fill):
    """A thick line with round ends."""
    d.line([a, b], fill=fill, width=width)
    r = width // 2
    for x, y in (a, b):
        d.ellipse([x - r, y - r, x + r, y + r], fill=fill)


def draw_mark(scale=1.0):
    """The mint "K" on a transparent square: a stem and two arms with round ends.
    scale < 1 shrinks it towards the centre."""
    mark = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(mark)
    c = S / 2
    p = lambda x, y: (c + (x + 6 - c) * scale, c + (y - c) * scale)   # +6 centres the mark
    w = lambda v: int(v * scale)
    stroke(d, p(340, 262), p(340, 762), w(128), 255)
    stroke(d, p(380, 560), p(684, 262), w(120), 255)
    stroke(d, p(478, 470), p(684, 762), w(120), 255)
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        gd.line([(0, y), (S, y)], fill=lerp(MINT_TOP, MINT_BOTTOM, y / S) + (255,))
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(grad, (0, 0), mark)
    return out


def draw_logo(rounded=True, scale=1.0):
    """rounded=False gives a full square for phones, which round the corners themselves
    (transparent corners would turn black on an iPhone home screen)."""
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    if rounded:
        ImageDraw.Draw(img).rounded_rectangle([0, 0, S - 1, S - 1], radius=232, fill=TILE + (255,))
    else:
        img.paste(TILE + (255,), (0, 0, S, S))
    img.alpha_composite(draw_mark(scale))
    return img


def main():
    (ROOT / "assets").mkdir(exist_ok=True)
    (ROOT / "web").mkdir(exist_ok=True)
    logo = draw_logo()
    logo.save(ROOT / "assets" / "logo.png")
    logo.save(ROOT / "assets" / "Kovrel.ico",
              sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    square = draw_logo(rounded=False).convert("RGB")
    for px in (180, 192, 512):  # home-screen icons: full square, no transparency
        square.resize((px, px), Image.LANCZOS).save(ROOT / "web" / f"icon-{px}.png")
    # Android "maskable" icon: the mark kept inside the safe circle so any mask shape fits.
    draw_logo(rounded=False, scale=0.72).convert("RGB").resize((512, 512), Image.LANCZOS).save(ROOT / "web" / "icon-maskable.png")
    logo.resize((64, 64), Image.LANCZOS).save(ROOT / "web" / "favicon.png")
    print("icons written")


if __name__ == "__main__":
    main()
