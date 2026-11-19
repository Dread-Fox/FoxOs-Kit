#!/usr/bin/env python3
"""FoxOS tagline refresh: swaps the text lines on the existing artwork.
The fox badge and backgrounds are left pixel-for-pixel untouched; only the
tagline strips are erased (vertical blend from the rows just above/below)
and redrawn. Outputs *-v2.png next to the originals."""
import os
from PIL import Image, ImageDraw, ImageFont

BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
CRIMSON = (196, 50, 60)
STEEL = (150, 156, 166)
WHITE = (242, 244, 246)
LINE1 = "BY FOX WROX"
LINE2 = "FIND THE EDGE  ···  SHARPEN IT!"
D = os.path.expanduser("~/foxos/") + "/"


def erase(img, x0, y0, x1, y1):
    """Fill rect by blending each column between the row above and below."""
    px = img.load()
    for x in range(x0, x1):
        top, bot = px[x, y0 - 1], px[x, y1]
        for y in range(y0, y1):
            t = (y - y0 + 1) / (y1 - y0 + 1)
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(top, bot))


def tracked(draw, cx, y, text, size, fill, track):
    """Draw centered text with letter-spacing `track` px; y = cap top."""
    f = ImageFont.truetype(BOLD, size)
    widths = [draw.textlength(c, font=f) for c in text]
    total = sum(widths) + track * (len(text) - 1)
    x = cx - total / 2
    asc = f.getbbox("H")[1]
    for c, w in zip(text, widths):
        draw.text((x, y - asc), c, font=f, fill=fill)
        x += w + track


def wallpaper():
    im = Image.open(D + "src/foxos-wallpaper.png").convert("RGBA")
    erase(im, 500, 700, 1420, 765)
    d = ImageDraw.Draw(im)
    tracked(d, 960, 717, LINE1, 34, CRIMSON, 14)
    tracked(d, 960, 785, LINE2, 20, STEEL, 6)
    im.save(D + "wallpaper/foxos-wallpaper-v2.png")


def login():
    # clean login bg (no baked-in fake form; the real greeter draws its own)
    im = Image.open(D + "wallpaper/_login_bg.png").convert("RGBA")
    erase(im, 640, 420, 1290, 460)
    d = ImageDraw.Draw(im)
    tracked(d, 960, 428, LINE1, 24, (176, 54, 63), 12)
    tracked(d, 960, 476, LINE2, 16, STEEL, 5)
    im.save(D + "wallpaper/foxos-login-v2.png")


def wordmark():
    im = Image.new("RGBA", (900, 220), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    tracked(d, 450, 14, "FoxOS", 96, WHITE, 2)
    tracked(d, 450, 136, LINE1, 26, CRIMSON, 12)
    tracked(d, 450, 186, LINE2, 15, STEEL, 4)
    im.save(D + "plymouth/wordmark-v2.png")


def boot_preview():
    """Preview of the splash: original mockup, bottom half re-laid out."""
    im = Image.open(D + "src/foxos-boot.png").convert("RGBA")
    # keep the mockup's badge + progress bar; replace text below the bar
    erase(im, 600, 680, 1320, 900)
    wm = Image.open(D + "plymouth/wordmark-v2.png")
    wm = wm.resize((630, 154), Image.LANCZOS)
    im.alpha_composite(wm, (960 - wm.width // 2, 696))
    im.save(D + "wallpaper/foxos-boot-v2.png")


wallpaper(); login(); wordmark(); boot_preview()
print("ok")
