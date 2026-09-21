#!/usr/bin/env python3
"""Audit staged modules and configuration without installing or loading them."""
import hashlib
import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'kernel/build-host-alto'
STAGE = ROOT / 'kernel/stage-host-alto'
RELEASE = '6.8.12-138-soaralto'


def config(path):
    return dict(line.split('=', 1) for line in path.read_text().splitlines()
                if line.startswith('CONFIG_') and '=' in line)


def main():
    original = config(Path('/boot/config-6.8.0-138-generic'))
    candidate = config(BUILD / '.config')
    allowed = {'CONFIG_CC_VERSION_TEXT', 'CONFIG_LOCALVERSION', 'CONFIG_VERSION_SIGNATURE',
               'CONFIG_SYSTEM_TRUSTED_KEYS', 'CONFIG_SYSTEM_REVOCATION_KEYS'}
    changed = {k: [original.get(k), candidate.get(k)]
               for k in sorted(original.keys() | candidate.keys())
               if original.get(k) != candidate.get(k)}
    unexpected = set(changed) - allowed
    if unexpected:
        raise RuntimeError(f'Unexpected config changes: {unexpected}')
    modules = sorted({line.split()[0] for line in Path('/proc/modules').read_text().splitlines()})
    modules = sorted(set(modules) | {'megaraid_sas', 'ngbe', 'cxl_core', 'cxl_pci',
                     'cxl_acpi', 'cxl_port', 'cxl_mem', 'cxl_pmu', 'device_dax', 'kmem',
                     'nvidia', 'nvidia_modeset', 'nvidia_drm', 'nvidia_uvm'})
    records = []
    for module in modules:
        result = subprocess.run(['modinfo', '-b', str(STAGE), '-k', RELEASE,
                                 '-F', 'vermagic', module], text=True, capture_output=True)
        records.append(dict(module=module, vermagic=result.stdout.strip(),
                            error=result.stderr.strip(),
                            ok=result.returncode == 0 and result.stdout.startswith(RELEASE + ' ')))
    artifacts = {str(p.relative_to(STAGE)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted((STAGE / 'boot').glob('*')) if p.is_file()}
    assert len(artifacts) >= 3, 'Missing boot artifacts'
    vm = (ROOT / 'results/host-alto-vm.log').read_text()
    vm_ok = 'ALTO_VM_PASS' in vm and 'ALTO_VM_FAIL' not in vm
    report = dict(time=time.time(), release=RELEASE, config_changes=changed,
                  modules=records, boot_sha256=artifacts, vm_pass=vm_ok,
                  initramfs_staged=(STAGE / 'boot' / f'initrd.img-{RELEASE}').exists(),
                  note='Module presence/vermagic and VM boot do not prove physical driver operation.')
    hashes = {str(p.relative_to(STAGE)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(STAGE.rglob('*')) if p.is_file() and not p.is_symlink()}
    (ROOT / 'results/host-alto-stage-sha256.json').write_text(json.dumps(hashes, indent=2) + '\n')
    output = ROOT / 'results/host-alto-audit.json'
    output.write_text(json.dumps(report, indent=2) + '\n')
    failed = [r['module'] for r in records if not r['ok']]
    print(json.dumps(dict(modules_checked=len(records), failed=failed, vm_pass=vm_ok,
                          report=str(output)), indent=2))
    if failed or not vm_ok:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
