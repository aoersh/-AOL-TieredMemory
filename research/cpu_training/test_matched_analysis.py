"""验证 AOL 比值汇总及完整窗口边界，避免平均比值或包含初始化。"""
import json
from pathlib import Path
import tempfile
import unittest
from analyze_matched_500 import interval_aol, rank, pearson

class MatchedAnalysisTests(unittest.TestCase):
    def test_ratio_of_sums_and_complete_intervals(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'training').mkdir()
            (root/'training/steps.json').write_text(json.dumps([
                dict(measured=True,start_ns=100,end_ns=400)]))
            events=['cycles','instructions','CYCLE_ACTIVITY.STALLS_L3_MISS',
                    'OFFCORE_REQUESTS_OUTSTANDING.CYCLES_WITH_DEMAND_DATA_RD',
                    'OFFCORE_REQUESTS.DEMAND_DATA_RD']
            lines=['# SOAR_MONOTONIC_REF_NS 0']
            for end,num,den in [(50,999,1),(150,999,1),(250,20,2),(350,50,10),(450,999,1)]:
                for event,value in zip(events,[100,100,10,num,den]):
                    lines.append(f'{end/1e9:.9f},{value},,{event},100,100.00,,')
            (root/'intervals.csv').write_text('\n'.join(lines)+'\n')
            value,count=interval_aol(root)
            self.assertEqual(count,2)
            self.assertAlmostEqual(value,70/12)
            (root/'intervals.csv').write_text('\n'.join(lines).replace(',100.00,',',50.00,')+'\n')
            with self.assertRaises(AssertionError): interval_aol(root)

    def test_ties_and_constant_feature(self):
        self.assertEqual(rank([2,1,2]),[2.5,1.0,2.5])
        self.assertIsNone(pearson([1,1,1],[1,2,3]))
        self.assertAlmostEqual(pearson([1,2,3],[3,2,1]),-1)

if __name__=='__main__':unittest.main(verbosity=2)
