#!/usr/bin/env python3
"""Set up rEFInd for two Windows installs (10 and 11). Run on Windows, as administrator.

Modes:
  apply   (default)
    1. Back up the EFI folder (without the BCD, which is in use) and export the BCD.
    2. Create S:\\EFI\\Win10 and S:\\EFI\\Win11, each with its own bootmanager and BCD,
       with the matching Windows as default and timeout 0 (boots straight into it).
    3. Copy the icons from assets\\icons to S:\\EFI\\refind\\icons.
    4. Rewrite the refind.conf entries: Windows 11 and Windows 10.
    5. Labels (--labels): "none" (default) uses logo-only icons and keeps the theme's label hidden,
       so no text is shown. "icon" uses icons with the system name written in the image.
       "native" shows rEFInd's own label under the selected entry (removes 'label' from 'hideui'
       in the theme) and uses logo-only icons.
    6. Set the Windows menu timeout (--timeout-menu; -1 leaves it unchanged).

  undo
    Remove S:\\EFI\\Win10 and S:\\EFI\\Win11, restore refind.conf and the timeout from a backup.

  verify
    Checklist of every part of the setup. Exit code 1 if anything is missing.

Usage (command prompt or PowerShell opened as administrator, with the Windows Python):
    python scripts\\apply_dual_windows.py
    python scripts\\apply_dual_windows.py --mode verify
    python scripts\\apply_dual_windows.py --mode undo
"""

import argparse
import ctypes
import datetime
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ASSETS = REPO / 'assets' / 'icons'
BACKUP_ROOT = REPO / 'backup'

ESP = Path('S:/EFI')
CONF = ESP / 'refind' / 'refind.conf'
ICONS = ESP / 'refind' / 'icons'
NEW_ICONS = ['os_win11.png', 'os_win10.png']
# icon sets in assets/icons: 'plain' = logo only (rEFInd draws the label), 'labeled' = name baked into the icon
ICON_SETS = {'none': 'plain', 'native': 'plain', 'icon': 'labeled'}
DONT_SCAN = 'dont_scan_dirs EFI/Win10,EFI/Win11,EFI/Microsoft,EFI/boot,EFI/Boot'
# rEFInd always checks the Windows bootmanager in its normal location, whatever dont_scan_dirs says,
# so it has to be hidden by file name ("+," keeps rEFInd's default list)
HIDE_MS_LOADER = '/EFI/Microsoft/Boot/bootmgfw.efi'
DONT_SCAN_FILES = f'dont_scan_files +,{HIDE_MS_LOADER}'
WIN_FOLDERS = [ESP / 'Win10', ESP / 'Win11']

# menu entries written by this script (the old Portuguese title is kept so it gets replaced)
MANAGED_TITLES = ('Windows 10', 'Windows 11', 'Windows (choose)', 'Windows (escolher)')

ENTRIES = r'''
menuentry "Windows 11" {
    icon \EFI\refind\icons\os_win11.png
    loader \EFI\Win11\bootmgfw.efi
}

menuentry "Windows 10" {
    icon \EFI\refind\icons\os_win10.png
    loader \EFI\Win10\bootmgfw.efi
}

'''


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except AttributeError:
        return False


def run(args, check=True):
    """Run a command and return its output. Raises an error if the exit code is not 0."""
    r = subprocess.run(args, capture_output=True, text=True, encoding='cp850', errors='replace')
    if check and r.returncode != 0:
        raise RuntimeError(f'Command failed ({r.returncode}): {" ".join(args)}\n{r.stdout}{r.stderr}')
    return r.stdout


def mount_esp():
    """Return True if this call mounted the EFI partition (so it must be unmounted at the end)."""
    if ESP.exists():
        return False
    run(['mountvol', 'S:', '/S'])
    if not ESP.exists():
        raise RuntimeError('Could not mount the EFI partition at S:.')
    return True


def unmount_esp():
    run(['mountvol', 'S:', '/D'], check=False)


def loader_guid(bcd_file, description):
    """Return the GUID of the loader whose description is `description` inside a BCD file."""
    txt = run(['bcdedit', '/store', str(bcd_file), '/enum', 'OSLOADER'])
    pattern = r'identifier\s+(\{[^}]+\})((?!identifier).)*?description\s+' + re.escape(description) + r'\b'
    m = re.search(pattern, txt, re.S)
    if not m:
        raise RuntimeError(f"Could not find '{description}' in BCD {bcd_file}")
    return m.group(1)


def default_guid(bcd_file):
    """GUID of the entry marked as default in a BCD file, or None."""
    try:
        txt = run(['bcdedit', '/store', str(bcd_file), '/enum', '{default}'])
    except RuntimeError:
        return None
    m = re.search(r'identifier\s+(\{[^}]+\})', txt)
    return m.group(1) if m else None


def bootmgr_timeout(bcd_file):
    """Timeout of the Windows Boot Manager inside a BCD file, or None."""
    txt = run(['bcdedit', '/store', str(bcd_file), '/enum', '{bootmgr}'], check=False)
    m = re.search(r'timeout\s+(\d+)', txt)
    return int(m.group(1)) if m else None


def new_backup():
    folder = BACKUP_ROOT / datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    folder.mkdir(parents=True, exist_ok=True)
    log = folder / 'robocopy.log'

    # the BCD is in use by Windows: copy the folder without it and export it with bcdedit.
    # robocopy returns 1-7 for success and 8+ for errors; what matters is refind.conf being copied.
    subprocess.run(
        ['robocopy', str(ESP), str(folder / 'EFI'), '/E', '/XF', 'BCD', '/R:0', '/W:0', f'/LOG:{log}'],
        capture_output=True,
    )
    if not (folder / 'EFI' / 'refind' / 'refind.conf').exists():
        raise RuntimeError(f'refind.conf was not copied to the backup. See {log}')

    run(['bcdedit', '/export', str(folder / 'bcd-backup')])

    m = re.search(r'timeout\s+(\d+)', run(['bcdedit', '/enum', '{bootmgr}']))
    if m:
        (folder / 'timeout-original.txt').write_text(m.group(1), encoding='ascii')

    (BACKUP_ROOT / 'last-backup.txt').write_text(str(folder), encoding='ascii')
    return folder


def create_windows_copy(name, description):
    """Create S:\\EFI\\<name> with its own bootmanager and BCD, default = <description>, no menu."""
    folder = ESP / name
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ESP / 'Microsoft' / 'Boot' / 'bootmgfw.efi', folder / 'bootmgfw.efi')

    bcd = folder / 'BCD'
    if bcd.exists():
        bcd.unlink()
    run(['bcdedit', '/export', str(bcd)])

    guid = loader_guid(bcd, description)
    run(['bcdedit', '/store', str(bcd), '/default', guid])
    run(['bcdedit', '/store', str(bcd), '/timeout', '0'])
    return guid


def read_conf():
    """Read refind.conf with LF line endings. Files written on Windows use CRLF,
    and the search patterns here expect LF."""
    return CONF.read_text(encoding='utf-8', errors='replace').replace('\r\n', '\n')


def remove_managed_entries(text):
    """Remove every menuentry block whose title this script manages, from its '{' to the matching '}'.
    Works line by line, so it does not depend on exact spacing or line breaks."""
    out = []
    skipping = False
    depth = 0
    for line in text.split('\n'):
        s = line.strip()
        if not skipping:
            m = re.match(r'menuentry\s+"(.+?)"\s*\{', s)
            if m and m.group(1) in MANAGED_TITLES:
                skipping = True
                depth = s.count('{') - s.count('}')
                if depth <= 0:
                    skipping = False
                continue
            out.append(line)
        else:
            depth += s.count('{') - s.count('}')
            if depth <= 0:
                skipping = False
    return '\n'.join(out).rstrip('\n') + '\n'


def theme_conf_path(conf_text):
    """Path (on the EFI partition) of the theme.conf included by refind.conf, or None."""
    m = re.search(r'(?m)^include[ \t]+(\S*theme\.conf)', conf_text)
    return ESP / 'refind' / m.group(1) if m else None


def set_native_labels(show):
    """Show or hide rEFInd's own text label under the selected entry, by editing 'hideui' in the theme."""
    theme = theme_conf_path(read_conf())
    if theme is None or not theme.exists():
        print('No theme.conf found; the label setting was left as it is.')
        return

    def edit(m):
        tokens = [t.strip() for t in m.group(1).split(',') if t.strip() and t.strip() != 'label']
        if not show:
            tokens.append('label')
        return 'hideui ' + (','.join(tokens) if tokens else 'none')

    text = theme.read_text(encoding='utf-8', errors='replace')
    theme.write_text(re.sub(r'(?m)^hideui[ \t]+([^\r\n]*)', edit, text), encoding='utf-8')


def update_refind_conf():
    # remove the entries and lines this script manages, so it can run again without duplicates
    t = remove_managed_entries(read_conf())
    t = re.sub(r'(?m)^dont_scan_(dirs|files).*\n?', '', t)

    # rEFInd must not list the copies or the original bootmanager on its own.
    # EFI/boot holds the fallback copy of the Windows bootmanager, which would show up as an extra entry
    t = t.rstrip('\n') + '\n\n' + DONT_SCAN + '\n' + DONT_SCAN_FILES + '\n' + ENTRIES
    CONF.write_text(t, encoding='utf-8')


def apply(menu_timeout, labels='none'):
    mounted = mount_esp()
    try:
        backup = new_backup()
        print(f'Backup created at: {backup}')

        g11 = create_windows_copy('Win11', 'Windows 11')
        g10 = create_windows_copy('Win10', 'Windows 10')
        print(f'Windows 11 (GUID): {g11}')
        print(f'Windows 10 (GUID): {g10}')

        ICONS.mkdir(parents=True, exist_ok=True)
        for n in NEW_ICONS:
            shutil.copy2(ASSETS / ICON_SETS[labels] / n, ICONS / n)

        update_refind_conf()
        set_native_labels(labels == 'native')
        print('Labels: ' + {
            'none': 'none (logos only)',
            'icon': 'name written in the icons',
            'native': "rEFInd's label under the selected entry",
        }[labels])

        if menu_timeout >= 0:
            run(['bcdedit', '/timeout', str(menu_timeout)])
            print(f'Windows menu timeout: {menu_timeout} s')

        print('\nEntries in refind.conf:')
        for line in CONF.read_text(encoding='utf-8').splitlines():
            if line.startswith(('menuentry', 'dont_scan_dirs')):
                print('  ' + line)
        print('\nDone. Restart and check the rEFInd menu.')
        print('To undo: python scripts\\apply_dual_windows.py --mode undo')
    finally:
        if mounted:
            unmount_esp()


def undo(backup_arg):
    if backup_arg:
        folder = Path(backup_arg)
    else:
        folder = Path((BACKUP_ROOT / 'last-backup.txt').read_text(encoding='ascii').strip())
    if not (folder / 'EFI' / 'refind' / 'refind.conf').exists():
        raise RuntimeError(f'Invalid or missing backup: {folder}. Nothing was changed.')

    mounted = mount_esp()
    try:
        for p in WIN_FOLDERS:
            if p.exists():
                shutil.rmtree(p)
        for n in NEW_ICONS:
            f = ICONS / n
            if f.exists():
                f.unlink()

        # restore the theme too (apply edits its 'hideui' line)
        backup_conf = (folder / 'EFI' / 'refind' / 'refind.conf').read_text(encoding='utf-8', errors='replace')
        m = re.search(r'(?m)^include[ \t]+(\S*theme\.conf)', backup_conf)
        if m and (folder / 'EFI' / 'refind' / m.group(1)).exists():
            shutil.copy2(folder / 'EFI' / 'refind' / m.group(1), ESP / 'refind' / m.group(1))

        shutil.copy2(folder / 'EFI' / 'refind' / 'refind.conf', CONF)

        tf = folder / 'timeout-original.txt'
        if tf.exists():
            to = tf.read_text(encoding='ascii').strip()
            run(['bcdedit', '/timeout', to])
            print(f'Timeout restored to {to} s')
        print(f'Undone from: {folder}')
    finally:
        if mounted:
            unmount_esp()


def verify():
    """Checklist: check every part of the setup. Return True if everything is applied."""
    items = []  # (ok, text)

    def item(ok, text):
        items.append((bool(ok), text))

    mounted = mount_esp()
    try:
        item(ESP.exists(), 'EFI partition mounted at S:')

        for name, description in (('Win11', 'Windows 11'), ('Win10', 'Windows 10')):
            folder = ESP / name
            bcd = folder / 'BCD'
            item((folder / 'bootmgfw.efi').exists() and bcd.exists(),
                 f'{name}: bootmgfw.efi and BCD copied')
            if bcd.exists():
                try:
                    expected = loader_guid(bcd, description)
                except RuntimeError:
                    expected = None
                item(expected and default_guid(bcd) == expected,
                     f'{name}: {description} is the BCD default')
                item(bootmgr_timeout(bcd) == 0, f'{name}: menu without delay (timeout 0)')
            else:
                item(False, f'{name}: BCD default (BCD missing)')
                item(False, f'{name}: menu without delay (BCD missing)')

        for n in NEW_ICONS:
            item((ICONS / n).exists(), f'Icon {n} copied')

        conf_text = read_conf() if CONF.exists() else ''
        for title, loader in (
            ('Windows 11', r'\EFI\Win11\bootmgfw.efi'),
            ('Windows 10', r'\EFI\Win10\bootmgfw.efi'),
        ):
            block = re.search(r'menuentry "' + re.escape(title) + r'" \{(.*?)\n\}', conf_text, re.S)
            item(block and loader in block.group(1), f'refind.conf: entry "{title}" points to {loader}')
            count = len(re.findall(r'menuentry "' + re.escape(title) + r'" \{', conf_text))
            item(count == 1, f'refind.conf: entry "{title}" appears once (no duplicate)')

        dsd = re.search(r'(?m)^dont_scan_dirs\s+(.*)$', conf_text)
        listed = {x.strip() for x in dsd.group(1).split(',')} if dsd else set()
        item({'EFI/Win10', 'EFI/Win11', 'EFI/Microsoft'} <= listed,
             'refind.conf: dont_scan_dirs excludes EFI/Win10, EFI/Win11 and EFI/Microsoft')
        item(any(x.lower() == 'efi/boot' for x in listed),
             'refind.conf: dont_scan_dirs excludes EFI/boot (no extra "Windows Boot Manager" entry)')
        item(re.search(r'(?m)^dont_scan_files\s+.*' + re.escape(HIDE_MS_LOADER), conf_text),
             f'refind.conf: dont_scan_files hides {HIDE_MS_LOADER} (no extra "Microsoft EFI boot" entry)')

        # informational only: does not count toward the result
        out = run(['bcdedit', '/enum', '{bootmgr}'], check=False)
        m = re.search(r'timeout\s+(\d+)', out)
        info_timeout = m.group(1) if m else '?'
    finally:
        if mounted:
            unmount_esp()

    print('Setup checklist:\n')
    for ok, text in items:
        print(f'  [{"OK" if ok else "MISSING"}] {text}')
    print(f'\nCurrent Windows menu timeout (bcdedit): {info_timeout} s')

    everything = all(ok for ok, _ in items)
    missing = sum(1 for ok, _ in items if not ok)
    print('\nRESULT: APPLIED' if everything else f'\nRESULT: NOT FULLY APPLIED ({missing} item(s) missing)')
    return everything


def main():
    p = argparse.ArgumentParser(description='Set up rEFInd for Windows 10 and 11.')
    p.add_argument('--mode', choices=['apply', 'undo', 'verify'], default='apply')
    p.add_argument('--timeout-menu', type=int, default=5,
                   help='timeout (s) of the Windows menu (choose); -1 leaves it unchanged')
    p.add_argument('--labels', choices=['none', 'icon', 'native'], default='none',
                   help="'none': logos only, no text; 'icon': name written in the icon; "
                        "'native': rEFInd's label under the selected entry")
    p.add_argument('--backup', default='', help='backup folder to restore (undo mode)')
    args = p.parse_args()

    if sys.platform != 'win32':
        sys.exit('This script must run on Windows (with the Windows Python).')
    if not is_admin():
        sys.exit('Run this script from a command prompt or PowerShell opened as administrator.')

    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        if args.mode == 'apply':
            apply(args.timeout_menu, args.labels)
        elif args.mode == 'undo':
            undo(args.backup)
        else:
            if not verify():
                sys.exit(1)
    except RuntimeError as e:
        sys.exit(f'ERROR: {e}')


if __name__ == '__main__':
    main()
