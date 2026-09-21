import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AllocatorTests(unittest.TestCase):
    def test_profile_and_concurrent_budgeted_realloc(self):
        for component in ('prof', 'interc'):
            subprocess.run(['make', '-C', str(ROOT / 'src/soar' / component)],
                           check=True, stdout=subprocess.DEVNULL)
        with tempfile.TemporaryDirectory(prefix='soar-allocator-') as tmp:
            tmp = Path(tmp)
            binary = tmp / 'probe'
            subprocess.run(['gcc', '-g', '-O0', '-fno-builtin', '-fno-pie', '-no-pie',
                            str(ROOT / 'tests/allocator_probe.c'), '-lpthread', '-o', str(binary)],
                           check=True)
            env = dict(os.environ, SOAR_CLOCK_MONOTONIC='1', SOAR_LOG_DIR=str(tmp),
                       LD_PRELOAD=str(ROOT / 'src/soar/prof/ldlib.so'))
            subprocess.run([str(binary)], env=env, check=True, timeout=30, capture_output=True)
            sites = set()
            for log in tmp.glob('data.raw.*'):
                for line in log.read_text().splitlines():
                    fields = line.split()
                    if len(fields) == 6 and fields[3] == '8192' and fields[5] == '1':
                        site = fields[1].strip('[]')
                        if int(site, 16) < 0x500000:
                            sites.add(site)
            self.assertTrue(sites)
            policy = tmp / 'policy.txt'
            policy.write_text(''.join(site + ' 0\n' for site in sorted(sites)))
            env.update(LD_PRELOAD=str(ROOT / 'src/soar/interc/ldlib.so'),
                       SOAR_POLICY=str(policy), SOAR_FAST_NODE='0', SOAR_SLOW_NODE='2',
                       SOAR_PLACEMENT_LOG='1')
            for budget in (0, 65536):
                env['SOAR_FAST_BYTES'] = str(budget)
                result = subprocess.run([str(binary)], env=env, check=True,
                                        timeout=30, capture_output=True, text=True)
                match = re.search(r'SOAR_BUDGET limit=(\d+) peak=(\d+) live=(\d+)', result.stderr)
                self.assertIsNotNone(match)
                self.assertLessEqual(int(match[2]), budget)
                self.assertEqual(int(match[3]), 0)
                self.assertIn('allocator semantics passed', result.stdout)
                if budget:
                    self.assertGreater(int(match[2]), 0)


if __name__ == '__main__':
    unittest.main()
