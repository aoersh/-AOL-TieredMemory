import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src/soar/run'), str(ROOT / 'run')]
from profile_intervals import read_intervals, sample_counts
from placement_policy import site_peaks, choose_sites


class ProfileTests(unittest.TestCase):
    def test_half_open_boundaries_and_short_lifetimes(self):
        samples = [0, 5, 9, 10, 11, 15, 20]
        allocs = [(0, 20, 100, 10, 'a'), (10, 15, 200, 10, 'b')]
        result = sample_counts(samples, [100, 109, 110, 200, 209, 100, 100],
                               allocs, [0, 10], [10, 20])
        self.assertEqual(result[0]['a'][0], 2)
        self.assertEqual(result[1]['a'][0], 1)
        self.assertEqual(result[1]['b'][0], 2)

    def test_real_intervals_and_quality(self):
        with tempfile.NamedTemporaryFile(mode='w+') as f:
            f.write('# SOAR_MONOTONIC_REF_NS 123000000000\n'
                    '0.501 100 cycles (75.00%)\n0.501 5 reads\n'
                    '0.731 70 cycles\n0.731 3 reads\n')
            f.flush()
            starts, ends, values, running = read_intervals(f.name, ['cycles', 'reads'])
        self.assertEqual(starts, [123000000000, 123501000000])
        self.assertEqual(ends, [123501000000, 123731000000])
        self.assertEqual(running, [75.0, 100.0])
        self.assertEqual(values['reads'], [5, 3])

    def test_missing_origin_is_rejected(self):
        with tempfile.NamedTemporaryFile(mode='w+') as f:
            f.write('0.5 100 cycles\n')
            f.flush()
            with self.assertRaisesRegex(ValueError, 'origin'):
                read_intervals(f.name, ['cycles'])

    def test_concurrent_allocations_budgeted_by_bytes(self):
        allocs = [dict(alloc_time=a, dealloc_time=b, size=s, obj_name=n)
                  for a, b, s, n in [(0, 10, 4097, 'a'), (5, 15, 4097, 'a'),
                                      (10, 20, 4096, 'b')]]
        peaks = site_peaks(allocs)
        self.assertEqual(peaks, {'a': 16384, 'b': 4096})
        selected, used = choose_sites({'a': 100, 'b': 1}, peaks, 8192)
        self.assertEqual(selected, {'b'})
        self.assertEqual(used, 4096)
        self.assertEqual(choose_sites({'a': 100}, peaks, 0), (set(), 0))


if __name__ == '__main__':
    unittest.main()
