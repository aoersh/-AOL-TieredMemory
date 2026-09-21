#!/usr/bin/env python3
"""Check by default; --install stages a bootable candidate but never reboots."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / 'kernel/stage-host-alto'
RELEASE = '6.8.12-138-soaralto'
STOCK = '6.8.0-138-generic'


def run(*args):
    return subprocess.check_output(args, text=True, env=dict(os.environ, LC_ALL='C'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true')
    args = parser.parse_args()
    if platform.release() != STOCK:
        raise SystemExit('Expected the original Ubuntu kernel to be running')
    if args.install and os.geteuid() != 0:
        raise SystemExit('Installation requires sudo; omit --install for a read-only check')
    if 'SecureBoot disabled' not in run('mokutil', '--sb-state'):
        raise SystemExit('This candidate requires Secure Boot disabled')
    audit = json.loads((ROOT / 'results/host-alto-audit.json').read_text())
    if audit['release'] != RELEASE or not audit['vm_pass'] or not all(r['ok'] for r in audit['modules']):
        raise SystemExit('Candidate audit did not pass')
    hashes = json.loads((ROOT / 'results/host-alto-stage-sha256.json').read_text())
    for name, expected in hashes.items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise SystemExit('Invalid artifact path')
        if hashlib.sha256((STAGE / relative).read_bytes()).hexdigest() != expected:
            raise SystemExit(f'Artifact changed after audit: {name}')
    grub_path = Path('/boot/grub/grub.cfg')
    grub = grub_path.read_text()
    entries = set(re.findall(r"'(gnulinux-" + re.escape(STOCK) + r"-advanced-([^']+))'", grub))
    if len(entries) != 1:
        raise SystemExit('Cannot uniquely identify stock GRUB entry')
    entry, uuid = entries.pop()
    submenu = 'gnulinux-advanced-' + uuid
    if "'" + submenu + "'" not in grub:
        raise SystemExit('Stock GRUB submenu missing')
    default = submenu + '>' + entry
    override = Path('/etc/default/grub.d/99-soaralto.cfg')
    destinations = [Path('/lib/modules') / RELEASE, override,
                    Path('/boot') / f'initrd.img-{RELEASE}']
    destinations += [Path('/boot') / p.name for p in (STAGE / 'boot').iterdir()]
    if any(p.exists() or p.is_symlink() for p in destinations):
        raise SystemExit('Candidate files or GRUB override already exist; inspect before retrying')
    if shutil.disk_usage('/boot').free < 1024**3 or shutil.disk_usage('/lib/modules').free < 2 * 1024**3:
        raise SystemExit('Need 1 GiB free in /boot and 2 GiB at /lib/modules')
    plan = dict(release=RELEASE, persistent_default=STOCK, grub_default_id=default,
                grub_menu_seconds=10, reboot=False, verified_files=len(hashes))
    print(json.dumps(plan, indent=2), flush=True)
    if not args.install:
        print('Read-only preflight passed; use sudo with --install to install without rebooting.')
        return
    backup = Path('/var/backups') / ('soaralto-' + time.strftime('%Y%m%d-%H%M%S'))
    backup.mkdir()
    shutil.copy2(grub_path, backup / 'grub.cfg')
    shutil.copy2(Path('/etc/default/grub'), backup / 'grub-default')
    (backup / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')
    # update-initramfs can invoke update-grub, so pin the old default first.
    override.parent.mkdir(parents=True, exist_ok=True)
    with override.open('x') as stream:
        stream.write(f'GRUB_DEFAULT="{default}"\nGRUB_TIMEOUT_STYLE=menu\nGRUB_TIMEOUT=10\n')
    override.chmod(0o644)
    try:
        shutil.copytree(STAGE / 'lib/modules' / RELEASE, Path('/lib/modules') / RELEASE,
                        symlinks=True)
        for source in (STAGE / 'boot').iterdir():
            shutil.copy2(source, Path('/boot') / source.name)
        run('depmod', '-a', RELEASE)
        output = run('update-initramfs', '-c', '-k', RELEASE)
        (backup / 'initramfs-build.log').write_text(output)
        contents = run('lsinitramfs', f'/boot/initrd.img-{RELEASE}')
        (backup / 'initramfs-contents.txt').write_text(contents)
        if not re.search(r'/megaraid_sas\.ko(?:\.(?:zst|xz|gz))?$', contents, re.M):
            raise RuntimeError('Root storage module missing from initramfs')
        run('update-grub')
        run('grub-script-check', '/boot/grub/grub.cfg')
        generated = grub_path.read_text()
        if f'set default="{default}"' not in generated:
            raise RuntimeError('Generated GRUB does not retain the stock default')
        if f'gnulinux-{RELEASE}-advanced-{uuid}' not in generated:
            raise RuntimeError('Candidate GRUB entry missing')
    except BaseException:
        print(f'Installation incomplete; retained files for inspection. Backup: {backup}', flush=True)
        print('Do not reboot until initramfs and GRUB are verified.', flush=True)
        raise
    print(f'Installed candidate without reboot. Stock kernel remains default. Backup: {backup}')
    print('First boot still requires recovery access and a separate one-time boot selection.')


if __name__ == '__main__':
    main()
