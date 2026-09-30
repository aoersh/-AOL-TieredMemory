"""保护线程归属、period 权重和解析失败行为，避免再次静默丢失样本。"""
import unittest
from analyze_kernel_profile import aggregate, parse_samples

class SampleAccountingTest(unittest.TestCase):
    def test_same_symbol_different_threads(self):
        raw = ('100/101 1.000000001: 10 \n'
               '\tffffffff81000001 copy_page ([kernel.kallsyms])\n\n'
               '100/102 1.000000002: 90 \n'
               '\tffffffff81000001 copy_page ([kernel.kallsyms])\n\n'
               '100/101 1.000000003: 30 \n'
               '\tffffffff81000002 native_queued_spin_lock_slowpath ([kernel.kallsyms])\n'
               '\tffffffff81000003 folio_batch_move_lru ([kernel.kallsyms])\n')
        samples = list(parse_samples(raw))
        all_threads = aggregate(samples)
        worker = aggregate(s for s in samples if s['tid'] == 101)
        self.assertEqual((all_threads['samples'], all_threads['period']), (3, 130))
        self.assertEqual((worker['samples'], worker['period']), (2, 40))
        self.assertEqual(worker['symbols'][0]['percent'], 75)
        self.assertEqual(worker['spin_paths'][0]['percent_of_group'], 75)
        self.assertNotIn('ffffffff', str(worker))

    def test_missing_frames_fails(self):
        with self.assertRaises(ValueError):
            aggregate(parse_samples('100/101 1.000000001: 10\n'))

    def test_bad_format_fails(self):
        with self.assertRaises(ValueError):
            list(parse_samples('unexpected perf format\n'))

if __name__ == '__main__':
    unittest.main()
