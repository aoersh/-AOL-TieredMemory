import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'run/install_host_alto_kernel.py'


class HostInstallTests(unittest.TestCase):
    def test_install_order_and_hash_guard(self):
        for scenario in ('success', 'changed_artifact', 'missing_storage'):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                spec = importlib.util.spec_from_file_location('host_install', SCRIPT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                base = Path(directory)
                system = base / 'system'
                project = base / 'project'
                stage = project / 'stage'
                release = module.RELEASE
                stock = module.STOCK
                for name in ('boot/grub', 'lib/modules', 'etc/default', 'var/backups'):
                    (system / name).mkdir(parents=True, exist_ok=True)
                (project / 'results').mkdir(parents=True)
                (stage / 'boot').mkdir(parents=True)
                (stage / 'lib/modules' / release).mkdir(parents=True)
                image = stage / 'boot' / ('vmlinuz-' + release)
                image.write_bytes(b'test kernel')
                (project / 'results/host-alto-stage-sha256.json').write_text(json.dumps({
                    str(image.relative_to(stage)): hashlib.sha256(image.read_bytes()).hexdigest()}))
                (project / 'results/host-alto-audit.json').write_text(json.dumps({
                    'release': release, 'vm_pass': True, 'modules': [{'ok': True}]}))
                old = f'gnulinux-{stock}-advanced-fixture'
                default = 'gnulinux-advanced-fixture>' + old
                grub = system / 'boot/grub/grub.cfg'
                original = f"submenu 'gnulinux-advanced-fixture'\nmenuentry '{old}'\n"
                grub.write_text(original)
                (system / 'etc/default/grub').write_text('GRUB_DEFAULT=0\n')
                override = system / 'etc/default/grub.d/99-soaralto.cfg'
                events = []

                def mapped_path(value):
                    path = Path(value)
                    return system / str(path).lstrip('/') if path.is_absolute() else path

                def fake_run(*args):
                    events.append(args[0])
                    if args[0] == 'mokutil':
                        return 'SecureBoot disabled\n'
                    if args[0] == 'update-initramfs':
                        self.assertIn(default, override.read_text())
                        self.assertTrue((system / 'boot' / image.name).exists())
                        return 'created'
                    if args[0] == 'lsinitramfs':
                        return '' if scenario == 'missing_storage' else 'usr/lib/modules/x/megaraid_sas.ko.zst\n'
                    if args[0] == 'update-grub':
                        grub.write_text(f'set default="{default}"\n' + original +
                                        f"'gnulinux-{release}-advanced-fixture'\n")
                    return ''

                if scenario == 'changed_artifact':
                    image.write_bytes(b'changed')
                with patch.object(module, 'ROOT', project), patch.object(module, 'STAGE', stage), \
                     patch.object(module, 'Path', mapped_path), patch.object(module, 'run', fake_run), \
                     patch.object(module.platform, 'release', return_value=stock), \
                     patch.object(module.os, 'geteuid', return_value=0), \
                     patch.object(module.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=10 * 1024**3)), \
                     patch('sys.argv', ['install', '--install']), contextlib.redirect_stdout(io.StringIO()):
                    if scenario == 'changed_artifact':
                        with self.assertRaisesRegex(SystemExit, 'Artifact changed'):
                            module.main()
                        self.assertFalse(override.exists())
                        self.assertEqual(events, ['mokutil'])
                    elif scenario == 'missing_storage':
                        with self.assertRaisesRegex(RuntimeError, 'Root storage module missing'):
                            module.main()
                        self.assertIn(default, override.read_text())
                        self.assertNotIn('update-grub', events)
                    else:
                        module.main()
                        self.assertEqual(events, ['mokutil', 'depmod', 'update-initramfs',
                                                  'lsinitramfs', 'update-grub', 'grub-script-check'])
                        self.assertIn(f'set default="{default}"', grub.read_text())


if __name__ == '__main__':
    unittest.main()
