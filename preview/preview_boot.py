#!/usr/bin/env python3
"""Preview the rEFInd menu from refind.conf and the theme.

Draws the theme background, the row of icons, the selection and the name of the
selected entry, at the screen size you choose (default 1920x1080).

Limitations: this is an approximation. The firmware sets the real video mode,
and the font and spacing of rEFInd are not reproduced exactly. Entries that
rEFInd detects on its own (not in refind.conf) are not shown. Use it to check
the theme, icons, names and entry order.

Usage:
    python preview\\preview_boot.py --refind-dir S:\\EFI\\refind
    python preview\\preview_boot.py --refind-dir refind-copy --selected 1 --open
"""

import argparse
import datetime
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# global rEFInd options that affect the drawing
GLOBAL_OPTIONS = {
    'timeout', 'banner', 'banner_scale', 'selection_big', 'selection_small',
    'icons_dir', 'big_icon_size', 'small_icon_size', 'hideui', 'default_selection',
}


def read_lines(path):
    """Return the useful lines of a file (no blank lines, no comments)."""
    text = Path(path).read_text(encoding='utf-8', errors='replace')
    return [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith('#')]


def split_config(lines):
    """Split global options from menu entries (menuentry ... { ... })."""
    globals_, entries = [], []
    current, depth = None, 0
    for s in lines:
        if current is None:
            m = re.match(r'menuentry\s+"?(.+?)"?\s*\{', s)
            if m:
                current = {'name': m.group(1).strip(), 'icon': None, 'loader': None, 'disabled': False}
                depth = 1
            else:
                globals_.append(s)
            continue

        depth += s.count('{') - s.count('}')
        if depth <= 0:
            entries.append(current)
            current = None
            continue
        # only the first level matters (submenus are ignored)
        if depth == 1:
            parts = s.split(None, 1)
            if parts[0] == 'icon' and len(parts) == 2:
                current['icon'] = parts[1].strip()
            elif parts[0] == 'loader' and len(parts) == 2:
                current['loader'] = parts[1].strip()
            elif s == 'disabled':
                current['disabled'] = True
    return globals_, entries


def build_options(globals_, refind_dir):
    """Build the final options, applying the theme (include) on top of the globals."""
    cfg = {}
    theme = None
    for s in globals_:
        parts = s.split(None, 1)
        if len(parts) < 2:
            continue
        key, value = parts[0], parts[1].strip()
        if key == 'include' and value.lower().endswith('theme.conf'):
            theme = value
        elif key in GLOBAL_OPTIONS:
            cfg[key] = value

    if theme:
        for s in read_lines(refind_dir / theme):
            parts = s.split(None, 1)
            if len(parts) == 2 and parts[0] in GLOBAL_OPTIONS:
                cfg[parts[0]] = parts[1].strip()
        # theme paths are relative to the refind folder
        cfg['_theme_folder'] = (refind_dir / theme).parent
    return cfg


def find_icon(refind_dir, cfg, file_name):
    """Find an icon: first in the theme's icons folder, then in the default refind folder."""
    candidates = []
    if cfg.get('_theme_folder') and cfg.get('icons_dir'):
        candidates.append(refind_dir / cfg['icons_dir'] / file_name)
    candidates.append(refind_dir / 'icons' / file_name)
    for c in candidates:
        if c.exists():
            return c
    return None


def icon_file_name(rel_path):
    """'\\EFI\\refind\\icons\\os_win11.png' -> 'os_win11.png'."""
    if not rel_path:
        return 'os_unknown.png'
    return re.split(r'[\\/]', rel_path)[-1]


def font(size):
    for candidate in ('C:/Windows/Fonts/arial.ttf', 'arial.ttf', 'DejaVuSans.ttf'):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def config_resolution(globals_):
    """Resolution from 'resolution W H' in refind.conf; 1920x1080 if absent or set to max/a mode number."""
    for s in globals_:
        parts = s.split()
        if len(parts) == 3 and parts[0] == 'resolution' and parts[1].isdigit() and parts[2].isdigit():
            return int(parts[1]), int(parts[2])
    return 1920, 1080


def render(refind_dir, width, height, selected, output, icon_size):
    globals_, entries = split_config(read_lines(refind_dir / 'refind.conf'))
    cfg = build_options(globals_, refind_dir)

    if width is None or height is None:
        cfg_w, cfg_h = config_resolution(globals_)
        width = width or cfg_w
        height = height or cfg_h

    entries = [e for e in entries if not e['disabled']]
    if not entries:
        raise SystemExit('No active menu entry (menuentry) found in refind.conf.')

    # background: the theme banner, stretched to the screen (banner_scale fillscreen)
    background = Image.new('RGB', (width, height), (0, 0, 0))
    if cfg.get('banner'):
        banner_path = refind_dir / cfg['banner']
        if banner_path.exists():
            picture = Image.open(banner_path).convert('RGB')
            background = picture.resize((width, height), Image.LANCZOS)
    img = background.convert('RGBA')

    # load the icons. Without --icon-size each one keeps its own size (what rEFInd draws);
    # with --icon-size they are all scaled to that size
    loaded = []
    for entry in entries:
        icon = find_icon(refind_dir, cfg, icon_file_name(entry['icon']))
        if icon is None:
            icon = find_icon(refind_dir, cfg, 'os_unknown.png')
        picture = Image.open(icon).convert('RGBA') if icon is not None else None
        if picture is not None and icon_size:
            picture = picture.resize((icon_size, icon_size), Image.LANCZOS)
        loaded.append(picture)

    known = [p for p in loaded if p is not None]
    box_w = max((p.width for p in known), default=128)
    box_h = max((p.height for p in known), default=128)
    spacing = int(box_w * 1.5)
    total = len(entries) * spacing - (spacing - box_w)
    x = (width - total) // 2
    y = height // 2 - box_h // 2

    sel_img = None
    if cfg.get('selection_big'):
        sel_path = refind_dir / cfg['selection_big']
        if sel_path.exists():
            sel_img = Image.open(sel_path).convert('RGBA')

    draw = ImageDraw.Draw(img)
    for i, picture in enumerate(loaded):
        if picture is None:
            continue
        cx = x + i * spacing
        # centre each icon in the shared box
        ox = cx + (box_w - picture.width) // 2
        oy = y + (box_h - picture.height) // 2
        if i == selected and sel_img is not None:
            sel = sel_img.resize((picture.width + 16, picture.height + 16), Image.LANCZOS)
            img.alpha_composite(sel, (ox - 8, oy - 8))
        img.alpha_composite(picture, (ox, oy))

    # name of the selected entry, centered under the row
    if 0 <= selected < len(entries):
        name = entries[selected]['name']
        f = font(28)
        text_width = draw.textlength(name, font=f)
        center_x = x + selected * spacing + box_w // 2
        draw.text((center_x - text_width / 2, y + box_h + 24), name, font=f, fill=(255, 255, 255, 255))

    # footer with the configured timeout (rEFInd counts down from this value)
    if cfg.get('timeout'):
        text = f"Timeout: {cfg['timeout']} s"
        f2 = font(20)
        text_width = draw.textlength(text, font=f2)
        draw.text(((width - text_width) / 2, height - 60), text, font=f2, fill=(200, 200, 200, 255))

    img.convert('RGB').save(output)
    return entries


def mount_s():
    """Mount the EFI partition at S: (needs administrator). Return True if it was mounted now."""
    r = subprocess.run(['mountvol', 'S:', '/S'], capture_output=True, text=True,
                       encoding='cp850', errors='replace')
    if r.returncode != 0 or not Path('S:/EFI').exists():
        sys.exit('Could not mount the EFI partition at S:. Run as administrator, '
                 'or use --refind-dir with a copy of the refind folder.')
    return True


def unmount_s():
    subprocess.run(['mountvol', 'S:', '/D'], capture_output=True)


def main():
    p = argparse.ArgumentParser(description='Preview the rEFInd menu.')
    p.add_argument('--refind-dir', default=r'S:\EFI\refind',
                   help='refind folder (from the mounted EFI partition, or a copy)')
    p.add_argument('--width', type=int, default=None, help='screen width (default: resolution from refind.conf)')
    p.add_argument('--height', type=int, default=None, help='screen height (default: resolution from refind.conf)')
    p.add_argument('--icon-size', type=int, default=None,
                   help='scale all icons to this size in pixels (default: each icon keeps its own size)')
    p.add_argument('--selected', type=int, default=0, help='index of the selected entry (0 = first)')
    p.add_argument('--output', default=str(Path(__file__).resolve().parent / 'menu-refind.png'))
    p.add_argument('--open', action='store_true', help='open the generated image (Windows)')
    args = p.parse_args()

    refind_dir = Path(args.refind_dir)

    # if the folder is on drive S: (EFI partition), mount it here and unmount at the end
    mounted = False
    if refind_dir.drive.upper() == 'S:' and not Path('S:/EFI').exists():
        mounted = mount_s()

    try:
        if not (refind_dir / 'refind.conf').exists():
            sys.exit(
                f'Could not find {refind_dir / "refind.conf"}.\n'
                'Use --refind-dir with a copy of the refind folder, or run as administrator '
                'so the script can mount the EFI partition at S:.'
            )

        conf = refind_dir / 'refind.conf'
        modified = datetime.datetime.fromtimestamp(conf.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')
        print(f'Read: {conf} (modified {modified})')

        entries = render(refind_dir, args.width, args.height, args.selected, args.output, args.icon_size)
        print(f'Entries drawn ({len(entries)}):')
        for i, e in enumerate(entries):
            marker = '>' if i == args.selected else ' '
            print(f'  {marker} {i}: {e["name"]}  (icon: {icon_file_name(e["icon"])})')

        for name, count in Counter(e['name'] for e in entries).items():
            if count > 1:
                print(f'WARNING: entry "{name}" appears {count} times in refind.conf. '
                      'Run apply_dual_windows.py --mode apply to clean it up.')
        print(f'Image saved to: {args.output}')
    finally:
        if mounted:
            unmount_s()

    if args.open and os.name == 'nt':
        os.startfile(args.output)


if __name__ == '__main__':
    main()
