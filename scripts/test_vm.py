#!/usr/bin/env python3
"""Hyper-V test VM that boots rEFInd from a copy of the EFI partition.

Used to check the menu (entries, icons, order) without rebooting the PC. The Windows installs
are NOT in the VM, so the Windows entries are listed but do not boot.

Stages:
  enable   Turn on the Hyper-V feature (restart the PC afterwards).
  create   Run apply_dual_windows, build a small disk with an EFI partition, copy the EFI folder,
           make rEFInd the fallback loader, create the VM (Secure Boot off, no network) and start it.
  update   Run apply_dual_windows, refresh the rEFInd folder (config, theme, icons) inside the
           existing VM disk and start the VM again.
  remove   Stop and delete the VM and its disk.

Use --skip-apply to not run apply_dual_windows first. Run as administrator, with the Windows Python.

Usage:
    python scripts\\test_vm.py --stage enable
    python scripts\\test_vm.py --stage create --vm-name rEFInd-test
    python scripts\\test_vm.py --stage update --vm-name rEFInd-test
    vmconnect localhost rEFInd-test
"""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

import apply_dual_windows as dual

VM_DIR = dual.REPO / 'vm'

CREATE_DISK = r'''
New-VHD -Path '__VHD__' -SizeBytes 512MB -Dynamic | Out-Null
$disk = Mount-VHD -Path '__VHD__' -Passthru | Get-Disk
Initialize-Disk -Number $disk.Number -PartitionStyle GPT
New-Partition -DiskNumber $disk.Number -UseMaximumSize -GptType '{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}' -AssignDriveLetter | Out-Null
$part = Get-Partition -DiskNumber $disk.Number | Where-Object { $_.Type -ne 'Reserved' } | Select-Object -First 1
Format-Volume -Partition $part -FileSystem FAT32 -NewFileSystemLabel 'ESP' -Confirm:$false | Out-Null
$part = Get-Partition -DiskNumber $disk.Number -PartitionNumber $part.PartitionNumber
Write-Output ('LETTER=' + $part.DriveLetter)
'''

MOUNT_DISK = r'''
$disk = Mount-VHD -Path '__VHD__' -Passthru | Get-Disk
$part = Get-Partition -DiskNumber $disk.Number | Where-Object { $_.Type -ne 'Reserved' -and $_.Size -lt 1GB } | Select-Object -First 1
if (-not $part.DriveLetter) {
    Add-PartitionAccessPath -DiskNumber $disk.Number -PartitionNumber $part.PartitionNumber -AssignDriveLetter
    $part = Get-Partition -DiskNumber $disk.Number -PartitionNumber $part.PartitionNumber
}
Write-Output ('LETTER=' + $part.DriveLetter)
'''

STOP_VM = r'''
$vm = Get-VM -Name '__VM__'
if ($vm.State -ne 'Off') { Stop-VM -Name '__VM__' -TurnOff -Force -Confirm:$false }
for ($n = 0; $n -lt 30 -and (Get-VM -Name '__VM__').State -ne 'Off'; $n++) { Start-Sleep -Seconds 1 }
if ((Get-VM -Name '__VM__').State -ne 'Off') { throw 'The VM did not turn off.' }
'''

CREATE_VM = r'''
New-VM -Name '__VM__' -Generation 2 -MemoryStartupBytes 2GB -VHDPath '__VHD__' | Out-Null
Get-VMNetworkAdapter -VMName '__VM__' | Remove-VMNetworkAdapter
Set-VMFirmware -VMName '__VM__' -EnableSecureBoot Off
Start-VM -Name '__VM__' | Out-Null
'''

REMOVE_VM = r'''
$vm = Get-VM -Name '__VM__' -ErrorAction SilentlyContinue
if ($vm) {
    if ($vm.State -ne 'Off') { Stop-VM -Name '__VM__' -TurnOff -Force -Confirm:$false }
    Remove-VM -Name '__VM__' -Force
}
'''


def powershell(script, vm='', vhd='', check=True):
    """Run a PowerShell snippet (Hyper-V cmdlets). Placeholders: __VM__ and __VHD__."""
    script = script.replace('__VM__', vm).replace('__VHD__', str(vhd))
    r = subprocess.run(
        ['powershell', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
         '-Command', "$ErrorActionPreference = 'Stop'; " + script],
        capture_output=True, text=True, encoding='cp850', errors='replace',
    )
    if check and r.returncode != 0:
        raise RuntimeError(f'PowerShell failed:\n{r.stdout}{r.stderr}')
    return r.stdout


def drive_letter(output):
    m = re.search(r'LETTER=([A-Za-z])', output)
    if not m:
        raise RuntimeError(f'Could not find the drive letter of the VM disk.\n{output}')
    return m.group(1).upper() + ':'


def vm_exists(name):
    out = powershell(f"Get-VM -Name '{name}' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name")
    return bool(out.strip())


def dismount(vhd):
    powershell(f"Dismount-VHD -Path '{vhd}' -ErrorAction SilentlyContinue", check=False)


def robocopy(src, dst, *extra):
    """Copy a folder tree. robocopy exit codes below 8 mean success."""
    r = subprocess.run(['robocopy', str(src), str(dst), '/E', *extra, '/R:0', '/W:0'], capture_output=True)
    if r.returncode >= 8:
        raise RuntimeError(f'robocopy failed ({r.returncode}): {src} -> {dst}')


def enable():
    powershell("Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V -All -NoRestart | Out-Null")
    print('Hyper-V enabled. Restart the PC, then run --stage create.')


def create(name, skip_apply, menu_timeout, labels):
    vhd = VM_DIR / f'{name}.vhdx'
    if not powershell("(Get-Service vmms -ErrorAction SilentlyContinue).Status").strip():
        raise RuntimeError('Hyper-V is not running. Run --stage enable and restart the PC first.')
    if vm_exists(name):
        raise RuntimeError(f"A VM named '{name}' already exists. Use --stage remove or another --vm-name.")
    if vhd.exists():
        raise RuntimeError(f'{vhd} already exists. Delete it or use another --vm-name.')

    if not skip_apply:
        dual.apply(menu_timeout, labels)
    VM_DIR.mkdir(parents=True, exist_ok=True)

    mounted_esp = dual.mount_esp()
    disk_mounted = False
    try:
        disk_mounted = True
        letter = drive_letter(powershell(CREATE_DISK, vhd=vhd))
        efi = Path(f'{letter}\\EFI')

        # BCD* is locked while Windows runs and the VM does not need it; 'vars' holds the real PC's PreviousBoot
        robocopy(dual.ESP, efi, '/XF', 'BCD*', '/XD', 'vars')

        # rEFInd becomes the fallback loader, which is the path the VM firmware boots by default
        (efi / 'Boot').mkdir(parents=True, exist_ok=True)
        shutil.copy2(efi / 'refind' / 'refind_x64.efi', efi / 'Boot' / 'bootx64.efi')

        # the Win10/Win11 copies need their own BCD (our files, not the locked live one)
        for w in ('Win10', 'Win11'):
            shutil.copy2(dual.ESP / w / 'BCD', efi / w / 'BCD')

        dismount(vhd)
        disk_mounted = False
        powershell(CREATE_VM, vm=name, vhd=vhd)
    except Exception:
        # do not leave a half-built disk mounted or in the way of the next run
        if disk_mounted:
            dismount(vhd)
        vhd.unlink(missing_ok=True)
        raise
    finally:
        if mounted_esp:
            dual.unmount_esp()
    print(f"VM '{name}' created and started. Open it with: vmconnect localhost {name}")


def update(name, skip_apply, menu_timeout, labels):
    vhd = VM_DIR / f'{name}.vhdx'
    if not vm_exists(name):
        raise RuntimeError(f"VM '{name}' not found. Run --stage create first (or pass the right --vm-name).")
    if not vhd.exists():
        raise RuntimeError(f'Disk {vhd} not found.')

    if not skip_apply:
        dual.apply(menu_timeout, labels)

    powershell(STOP_VM, vm=name)
    mounted_esp = dual.mount_esp()
    try:
        letter = drive_letter(powershell(MOUNT_DISK, vhd=vhd))
        robocopy(dual.ESP / 'refind', f'{letter}\\EFI\\refind', '/XD', 'vars')
        print(f'Updated {letter}\\EFI\\refind from {dual.ESP / "refind"}')
    finally:
        dismount(vhd)
        if mounted_esp:
            dual.unmount_esp()
    powershell("Start-VM -Name '__VM__' | Out-Null", vm=name)
    print(f"VM '{name}' started. Open it with: vmconnect localhost {name}")


def remove(name):
    vhd = VM_DIR / f'{name}.vhdx'
    if vm_exists(name):
        powershell(REMOVE_VM, vm=name)
        print(f"VM '{name}' removed.")
    dismount(vhd)
    if vhd.exists():
        vhd.unlink()
        print('Disk removed.')


def main():
    p = argparse.ArgumentParser(description='Hyper-V test VM for the rEFInd menu.')
    p.add_argument('--stage', choices=['enable', 'create', 'update', 'remove'], default='update')
    p.add_argument('--vm-name', default='rEFInd-test')
    p.add_argument('--skip-apply', action='store_true', help='do not run apply_dual_windows first')
    p.add_argument('--timeout-menu', type=int, default=5, help='passed to apply_dual_windows')
    p.add_argument('--labels', choices=['none', 'icon', 'native'], default='none', help='passed to apply_dual_windows')
    args = p.parse_args()

    if sys.platform != 'win32':
        sys.exit('This script must run on Windows (with the Windows Python).')
    if not dual.is_admin():
        sys.exit('Run this script from a command prompt or PowerShell opened as administrator.')

    try:
        if args.stage == 'enable':
            enable()
        elif args.stage == 'create':
            create(args.vm_name, args.skip_apply, args.timeout_menu, args.labels)
        elif args.stage == 'update':
            update(args.vm_name, args.skip_apply, args.timeout_menu, args.labels)
        else:
            remove(args.vm_name)
    except RuntimeError as e:
        sys.exit(f'ERROR: {e}')


if __name__ == '__main__':
    main()
