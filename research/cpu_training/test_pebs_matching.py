"""防止地址重用、半开边界和低质量 interval 污染 PEBS 归因。"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

class MatchingTests(unittest.TestCase):
    def test_address_reuse_half_open_and_missing_counts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'training').mkdir()
            records = [dict(id=2,step=0,measured=False,pack_ns=100,unpack_ns=120,
                            release_ns=200,address=4096,bytes=16),
                       dict(id=6,step=1,measured=True,pack_ns=200,unpack_ns=220,
                            release_ns=300,address=4096,bytes=16),
                       dict(id=10,step=1,measured=True,pack_ns=200,unpack_ns=220,
                            release_ns=300,address=8192,bytes=16)]
            (root/'training/tensor-lifetimes.json').write_text(json.dumps(records))
            events=['cycles','instructions','CYCLE_ACTIVITY.STALLS_L3_MISS',
                    'OFFCORE_REQUESTS_OUTSTANDING.CYCLES_WITH_DEMAND_DATA_RD',
                    'OFFCORE_REQUESTS.DEMAND_DATA_RD']
            (root/'intervals.csv').write_text('# SOAR_MONOTONIC_REF_NS 0\n'+''.join(
                f'0.000000400,{"<not counted>" if i==0 else "100"},,{e},400,100.00,,\n'
                for i,e in enumerate(events)))
            (root/'samples.txt').write_text('0.000000099: 1000 100\n0.000000100: 1000 100\n'
                '0.000000200: 1000 100\n0.000000230: 1010 100\n0.000000300: 1000 100\n')
            subprocess.run([sys.executable,str(Path(__file__).with_name('analyze_pebs_probe.py')),str(root)],
                           check=True,capture_output=True)
            report=json.loads((root/'address-summary.json').read_text())
            self.assertEqual(report['stats']['matched_samples'],2)
            self.assertEqual(report['stats']['warmup_matched'],1)
            self.assertEqual(report['stats']['measured_matched'],1)
            self.assertEqual(report['stats']['matched_invalid_interval'],1)
            by_id={r['id']:r for r in report['objects']}
            self.assertEqual(by_id[6]['samples'],1)
            self.assertIsNone(by_id[6]['mean_sample_context_aol'])
            self.assertEqual(by_id[10]['samples'],0)

if __name__=='__main__': unittest.main(verbosity=2)
