"""Draws the app icon (icon.ico, icon.png) and the Explorer tick (printed.ico).
Needs Pillow; only run it to change the artwork: python tools/make_icons.py"""
import os

from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIZES = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)]


def sphere(d, im, cx, cy, r, base, s):
    """A shaded ball: base colour, darker rim, a soft highlight."""
    dark = tuple(int(c * 0.62) for c in base)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=dark + (255,))
    d.ellipse([cx - r * 0.9, cy - r * 0.95, cx + r * 0.82, cy + r * 0.8], fill=base + (255,))
    hl = Image.new('RGBA', im.size, (0, 0, 0, 0))
    hd = ImageDraw.Draw(hl)
    hr = r * 0.3
    hx, hy = cx - r * 0.38, cy - r * 0.42
    hd.ellipse([hx - hr, hy - hr, hx + hr, hy + hr], fill=(255, 255, 255, 150))
    im.alpha_composite(hl.filter(ImageFilter.GaussianBlur(s / 90)))


def app_icon(size):
    s = size * 4
    im = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    u = s / 32
    # build plate: dark rounded square with a green rim
    d.rounded_rectangle([1 * u, 1 * u, 31 * u, 31 * u], radius=6 * u, fill=(0, 174, 66, 255))
    d.rounded_rectangle([2.4 * u, 2.4 * u, 29.6 * u, 29.6 * u], radius=4.8 * u, fill=(36, 38, 43, 255))
    # three colour groups, sorted onto the plate
    sphere(d, im, 11 * u, 12 * u, 6.4 * u, (226, 52, 47), s)
    d = ImageDraw.Draw(im)
    sphere(d, im, 21.5 * u, 12.5 * u, 5.6 * u, (240, 240, 236), s)
    d = ImageDraw.Draw(im)
    sphere(d, im, 16 * u, 22 * u, 5.8 * u, (250, 196, 30), s)
    return im.resize((size, size), Image.LANCZOS)


def tick_icon(size):
    s = size * 4
    im = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    u = s / 32
    dark, body = (21, 128, 61, 255), (34, 168, 83, 255)
    d.rounded_rectangle([2 * u, 6 * u, 14 * u, 12 * u], radius=2 * u, fill=dark)     # tab
    d.rounded_rectangle([2 * u, 9 * u, 30 * u, 28 * u], radius=3 * u, fill=body)     # body
    w = max(1, round(3.4 * u))
    d.line([(9 * u, 18.5 * u), (14 * u, 23.5 * u), (23.5 * u, 13 * u)], fill='white', width=w, joint='curve')
    for x, y in [(9 * u, 18.5 * u), (23.5 * u, 13 * u)]:
        d.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill='white')
    return im.resize((size, size), Image.LANCZOS)


if __name__ == '__main__':
    big = app_icon(256)
    big.save(os.path.join(ROOT, 'icon.ico'), sizes=SIZES)
    big.save(os.path.join(ROOT, 'icon.png'))
    tick_icon(256).save(os.path.join(ROOT, 'printed.ico'), sizes=SIZES)
    print('icon.ico, icon.png, printed.ico written')
