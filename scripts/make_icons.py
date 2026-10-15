#!/usr/bin/env python3
"""Build the Windows 10 / 11 menu icons from the logos in assets/icons/source.

Two sets are written (canvas x canvas, transparent):
  assets/icons/plain/    logo only. Used with rEFInd's own label (apply_dual_windows --labels native).
  assets/icons/labeled/  logo with the system name written under it. rEFInd only shows the label of the
                         selected entry, so this is the way to keep both names visible
                         (apply_dual_windows --labels icon).

Does not need administrator rights. Needs Pillow (python -m pip install -r preview/requirements.txt).

Usage:
    python scripts\\make_icons.py
    python scripts\\make_icons.py --logo 56 --font-size 14
"""

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent
SOURCES = REPO / 'assets' / 'icons' / 'source'
OUT = REPO / 'assets' / 'icons'

ICONS = [
    # (output file, source file, label)
    ('os_win11.png', 'windows11.png', 'Windows 11'),
    ('os_win10.png', 'windows10.png', 'Windows 10'),
]


def load_font(size):
    for candidate in ('C:/Windows/Fonts/segoeuib.ttf', 'C:/Windows/Fonts/arialbd.ttf', 'DejaVuSans-Bold.ttf'):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def build_icon(source, label, canvas, logo, font_size, top):
    img = Image.new('RGBA', (canvas, canvas), (0, 0, 0, 0))

    # logo scaled to fit its area (keeping the aspect ratio) and centered in it
    picture = Image.open(source).convert('RGBA')
    picture.thumbnail((logo, logo), Image.LANCZOS)
    x = (canvas - picture.width) // 2
    if label:
        y = top + (logo - picture.height) // 2
    else:
        y = (canvas - picture.height) // 2  # logo only: centered in the whole icon
    img.alpha_composite(picture, (x, y))

    # label under the logo
    if label:
        draw = ImageDraw.Draw(img)
        font = load_font(font_size)
        left, _, right, _ = draw.textbbox((0, 0), label, font=font)
        draw.text(((canvas - (right - left)) / 2 - left, top + logo + 8), label,
                  font=font, fill=(30, 30, 30, 255))
    return img


def main():
    p = argparse.ArgumentParser(description='Build the Windows menu icons.')
    p.add_argument('--canvas', type=int, default=128, help='icon size in pixels (theme icons are 128)')
    p.add_argument('--logo', type=int, default=64, help='size of the logo area inside the icon')
    p.add_argument('--font-size', type=int, default=16, help='label height in pixels')
    p.add_argument('--top', type=int, default=12, help='space above the logo (labeled icons)')
    args = p.parse_args()

    # two sets: 'plain' (logo only; rEFInd draws the label) and 'labeled' (name written in the image)
    for folder, with_label in (('plain', False), ('labeled', True)):
        (OUT / folder).mkdir(parents=True, exist_ok=True)
        for out_name, source_name, label in ICONS:
            icon = build_icon(SOURCES / source_name, label if with_label else None,
                              args.canvas, args.logo, args.font_size, args.top)
            icon.save(OUT / folder / out_name)
            print(f'Wrote {folder}/{out_name} ({args.canvas} x {args.canvas})')


if __name__ == '__main__':
    main()
