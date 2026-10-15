# rEFInd with Windows 10 and Windows 11

A set of scripts that configures [rEFInd](https://www.rodsbooks.com/refind/) as the boot menu of a PC with **Windows 10 and Windows 11 installed side by side**, so that each system has its own entry with its own icon and boots **directly**, without going through the Windows boot menu.

The menu ends up with exactly two entries:

- **Windows 11**: boots Windows 11 immediately
- **Windows 10**: boots Windows 10 immediately

Without this setup, rEFInd lists a single "Windows" entry and the Windows Boot Manager then asks which system to start, which means choosing twice. The repository also has a menu preview and a Hyper-V test VM, so the result can be checked without rebooting the PC.

> **Warning:** this changes the EFI system partition and the boot configuration. It was developed and tested on a single machine (UEFI, GPT, Windows 10 and 11 on separate partitions). Every run makes a backup first and there is an undo mode, but keep a Windows installation USB at hand.

## How it works

1. **One Windows Boot Manager per system.** The script creates `EFI\Win10` and `EFI\Win11` on the EFI partition. Each folder holds a copy of `bootmgfw.efi` and its own BCD (boot configuration) store, exported from the real one, where the matching Windows is the default entry and the timeout is 0. On the machine this was built on, the copy started that Windows without asking. That is observed behavior, not something Microsoft documents, so check it with `--mode verify` and a real reboot on your own machine.
2. **Two rEFInd entries** (`menuentry "Windows 11"` and `"Windows 10"`) point to those copies and use their own icons.
3. **Hiding the automatic entries.** rEFInd always looks for the Windows bootmanager in its normal location, whatever `dont_scan_dirs` says, so the script also uses `dont_scan_files` to hide `\EFI\Microsoft\Boot\bootmgfw.efi`, and `dont_scan_dirs` for the copies and for the fallback `EFI\boot`.
4. **Nothing in the real Windows boot is replaced.** The original BCD and `EFI\Microsoft\Boot` are left alone. The only change to the live BCD is the menu timeout (optional).

## Requirements

- **UEFI + GPT** with **Windows 10 and Windows 11** both installed and listed in the BCD. The script looks for the descriptions `Windows 10` and `Windows 11` in English, as `bcdedit` shows them. Check with `bcdedit /enum OSLOADER` in an administrator prompt.
- **rEFInd installed** on the EFI partition, in `EFI\refind` (see [rEFInd's installation guide](https://www.rodsbooks.com/refind/installing.html)), and set as the first boot option in the firmware. **Secure Boot must be off**, because rEFInd is not signed by default.
- **The `rEFInd-minimal` theme** ([EvanPurkhiser/rEFInd-minimal](https://github.com/EvanPurkhiser/rEFInd-minimal)) installed at `EFI\refind\themes\rEFInd-minimal\` and enabled in `refind.conf`:

  ```
  include themes/rEFInd-minimal/theme.conf
  ```

  `theme.conf`, `background.png` and the `icons` folder must sit **directly** inside that folder. The ZIP downloaded from GitHub extracts to `rEFInd-minimal-main\`, which is one level too deep; move the contents up, or the theme is silently ignored.
- **Optional:** a fixed resolution in `refind.conf`, for example `resolution 1920 1080`. Whether it works depends on the video modes the firmware exposes (see Troubleshooting).
- **Windows Python 3.8 or newer** (not the WSL one) and an **administrator** command prompt or PowerShell. The scripts use the Windows tools `mountvol`, `bcdedit` and `robocopy`.
- **Pillow**, for the icon generator and the preview: `python -m pip install -r preview\requirements.txt`.
- **Hyper-V** (Windows Pro/Enterprise), only for the test VM.

The installation of rEFInd and of the theme is not done by these scripts. They expect both to be in place.

## Quick start

From a command prompt opened as administrator, inside the repository folder:

```bat
python -m pip install -r preview\requirements.txt
python scripts\apply_dual_windows.py
python scripts\apply_dual_windows.py --mode verify
```

Restart and check the rEFInd menu. The first command makes a backup in `backup\<date>`, creates the copies, installs the icons and rewrites the entries. Running it again is safe: it replaces its own entries instead of duplicating them.

## Commands

```bat
python scripts\apply_dual_windows.py [--mode apply|verify|undo] [--timeout-menu N] [--labels none|icon|native] [--backup DIR]
```

| Option | What it does |
| --- | --- |
| `--mode apply` (default) | Backs up, creates `EFI\Win10` and `EFI\Win11`, copies the icons, rewrites `refind.conf`. |
| `--mode verify` | Prints a checklist (`[OK]` / `[MISSING]`) of every part of the setup, and exits with code 1 if something is missing. |
| `--mode undo` | Removes `EFI\Win10` and `EFI\Win11`, and restores `refind.conf`, the theme's `theme.conf` and the menu timeout from the latest backup (or from `--backup DIR`). |
| `--timeout-menu N` | Timeout in seconds of the original Windows boot menu (default 5; `-1` leaves it as it is). |
| `--labels` | `none` (default): logo-only icons, no text. `icon`: the system name is written in the icon image. `native`: rEFInd's own label under the selected entry. |

About `--labels native`: rEFInd builds that text as "Boot *name* from *volume*". The wording is hard-coded in rEFInd's source and cannot be changed from the configuration or the theme, only by patching and rebuilding rEFInd. That is why `none` is the default.

The scripts mount the EFI partition at `S:` when they need it and unmount it at the end, so no manual `mountvol` is needed.

## Icons

The Windows 10 / 11 icons come from the logos in `assets/icons/source`:

```bat
python scripts\make_icons.py
```

It writes two sets of 128×128 transparent PNGs, the same size as the theme's icons:

- `assets/icons/plain/`: logo only
- `assets/icons/labeled/`: logo with the system name written under it

Options: `--logo` (logo size, default 64), `--font-size` (default 16), `--canvas` (default 128). Run `apply_dual_windows.py` afterwards to copy the icons to the EFI partition. The Windows logos are trademarks of Microsoft.

## Menu preview

Draws the menu from `refind.conf` and the theme: background, icons, entries and order. It uses the EFI partition (mounting `S:` itself when run as administrator) or any copy of the `refind` folder:

```bat
python preview\preview_boot.py --open
python preview\preview_boot.py --refind-dir C:\path\to\refind-copy --selected 1
```

Options: `--width`/`--height` (default: the `resolution` from `refind.conf`), `--icon-size` (scale all icons), `--selected`, `--output`. It also warns about entries that appear twice in `refind.conf`.

It is an approximation. The firmware sets the real video mode, and rEFInd's font and spacing are not reproduced exactly. Entries that rEFInd detects by itself (not written in `refind.conf`) are not drawn.

## Test VM (Hyper-V)

`scripts/test_vm.py` builds a Generation 2 VM that boots rEFInd from a copy of the EFI partition, which shows the real menu, including the entries rEFInd detects on its own. The Windows installs are not in the VM, so the entries are listed but do not boot. Run it as administrator:

```bat
:: once: enable Hyper-V, then restart the PC
python scripts\test_vm.py --stage enable

:: create and start the VM (runs apply_dual_windows first)
python scripts\test_vm.py --stage create --vm-name rEFInd-test
vmconnect localhost rEFInd-test

:: after changing the setup: apply it, refresh the rEFInd folder inside the same VM and restart it
python scripts\test_vm.py --stage update --vm-name rEFInd-test

:: delete the VM and its disk
python scripts\test_vm.py --stage remove --vm-name rEFInd-test
```

`create` and `update` run `apply_dual_windows.py` first, so the VM always shows the current setup. Use `--skip-apply` to skip that. The VM disk is built without the BCD files that Windows keeps locked and without rEFInd's `vars` folder (it would show a stale "previous boot" entry).

If you choose an entry in the VM you get a blue "Recovery / 0xc000000f" screen. That is expected: there is no Windows in the VM to start.

## Recovery

- Every `apply` writes a backup to `backup\<date>`: the `EFI` folder (without the locked BCD files) and the exported BCD (`bcd-backup`). `backup\last-backup.txt` points to the latest one.
- `python scripts\apply_dual_windows.py --mode undo` reverts the setup.
- If Windows does not start at all, boot a Windows installation USB, open the recovery command prompt and run `bcdedit /import "<path>\bcd-backup"` (`mountvol S: /S` mounts the EFI partition there). The original BCD is not modified by `apply`, except for the optional timeout.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| The theme does not show up | The theme folder is one level too deep (`rEFInd-minimal\rEFInd-minimal-main\...`), or the `include` line in `refind.conf` is missing or commented. |
| rEFInd is stuck at 800×600 | The firmware only exposes that video mode to rEFInd, so `resolution` cannot help. In the BIOS, turn CSM off, set the primary display to the PCIe graphics card, and plug the monitor into the graphics card, not the motherboard. |
| An extra "Windows" entry with the theme's generic icon | rEFInd always scans `\EFI\Microsoft\Boot\bootmgfw.efi`. The script hides it with `dont_scan_files`; `--mode verify` checks that line. |
| The same entry appears twice | An older `refind.conf` with leftover entries. Run `apply` again: it removes every entry it manages before writing them. |
| `Windows 10`/`Windows 11` not found in the BCD | `bcdedit` shows the descriptions in another language, or the entries have other names. The script stops before touching `refind.conf`; run `--mode undo` to clean what was created and check `bcdedit /enum OSLOADER`. |
| "The process cannot access the file ... BCD" | The live BCD is locked while Windows runs. The scripts already export it with `bcdedit /export` and skip `BCD*` when copying. |

## Repository layout

| Path | What it is |
| --- | --- |
| `scripts/apply_dual_windows.py` | Applies, verifies or undoes the setup |
| `scripts/make_icons.py` | Builds the Windows 10 / 11 icons from `assets/icons/source` |
| `scripts/test_vm.py` | Creates, updates or removes the Hyper-V test VM |
| `preview/preview_boot.py` | Draws a preview of the menu |
| `assets/icons/` | `source/` (original logos), `plain/` and `labeled/` (generated icons) |
| `backup/`, `vm/` | Backups and VM disks created by the scripts (not versioned) |
