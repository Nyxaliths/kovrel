"""Builds docs/social-preview.png (1280x640), the picture GitHub shows when the repo is shared.

    python tools/make_social_preview.py

Needs the screenshots in docs/ (see tools/demo_server.py and tools/demo_windows.py). Upload the result in
the repository's Settings > General > Social preview.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
W, H = 1280, 640
BG, FG, MUTED, PRIMARY = (14, 16, 19), (236, 238, 241), (143, 150, 161), (47, 214, 163)


def font(size, bold=False):
    for name in ("segoeuib.ttf" if bold else "segoeui.ttf", "arialbd.ttf" if bold else "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def phone(path, height):
    """A screenshot with rounded corners and a thin edge, scaled to the given height."""
    im = Image.open(path).convert("RGB")
    im = im.resize((round(im.width * height / im.height), height), Image.LANCZOS)
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, im.width - 1, im.height - 1), 26, fill=255)
    out = Image.new("RGBA", im.size)
    out.paste(im, (0, 0), mask)
    ImageDraw.Draw(out).rounded_rectangle((0, 0, im.width - 1, im.height - 1), 26, outline=(70, 77, 88), width=3)
    return out


def shadowed(canvas, img, xy):
    sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    x, y = xy
    ImageDraw.Draw(sh).rounded_rectangle((x, y + 14, x + img.width, y + img.height + 14), 26, fill=(0, 0, 0, 170))
    canvas.alpha_composite(sh.filter(ImageFilter.GaussianBlur(18)))
    canvas.alpha_composite(img, xy)


canvas = Image.new("RGBA", (W, H), BG)
d = ImageDraw.Draw(canvas)
# soft mint glow behind the phones
glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
ImageDraw.Draw(glow).ellipse((700, 40, 1260, 600), fill=(47, 214, 163, 38))
canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(70)))

logo = Image.open(ROOT / "assets" / "logo.png").convert("RGBA").resize((96, 96), Image.LANCZOS)
canvas.alpha_composite(logo, (80, 110))
d.text((196, 118), "Kovrel", font=font(76, True), fill=FG)
d.text((80, 262), "Control your Windows PC\nfrom your phone.", font=font(52, True), fill=FG, spacing=8)
d.text((80, 410), "No phone app. No account. No cloud.", font=font(30), fill=PRIMARY)
d.text((80, 474), "Free and open source (MIT)", font=font(24), fill=MUTED)

a = phone(DOCS / "screenshot-screen.png", 520)
b = phone(DOCS / "screenshot-media.png", 520)
shadowed(canvas, b, (1010, 60))
shadowed(canvas, a, (770, 90))
canvas.convert("RGB").save(DOCS / "social-preview.png", optimize=True)
print("saved", DOCS / "social-preview.png")
