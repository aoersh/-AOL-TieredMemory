import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('alto', ROOT / 'run/bc-urand/set_scan_scale.py')
alto = importlib.util.module_from_spec(spec)
spec.loader.exec_module(alto)


class AltoTests(unittest.TestCase):
    def test_online_restores_original_on_process_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'perf.log'
            knob = Path(directory) / 'scale'
            knob.write_text('8\n')
            log.write_text('0.5 10 ' + alto.DEMAND + '\n0.5 300 ' + alto.BUSY +
                           '\n0.5 2000 ' + alto.OUTSTANDING + '\n')
            def paths(value):
                if value == '/proc/sys/kernel/numa_balancing_pte_scale':
                    return knob
                return Path(value)
            with patch.object(alto, 'Path', side_effect=paths), \
                 patch('sys.argv', ['alto', str(log), '--pid', '123']), \
                 patch.object(alto.os, 'kill', side_effect=ProcessLookupError), \
                 patch.object(alto.signal, 'signal'):
                alto.main()
            self.assertEqual(knob.read_text(), '8\n')

    def test_original_thresholds_and_invalid_data(self):
        for aol, expected in [(30, 0), (40, 0), (41, 1), (50, 1), (60, 2),
                              (80, 4), (100, 8), (101, 16)]:
            self.assertEqual(alto.decision({alto.DEMAND: 10, alto.BUSY: aol * 10,
                             alto.OUTSTANDING: 2000})['pte_scale'], expected)
        self.assertNotIn('pte_scale', alto.decision({alto.DEMAND: 0, alto.BUSY: 1,
                                                   alto.OUTSTANDING: 2}))
        self.assertNotIn('pte_scale', alto.decision({alto.DEMAND: 10, alto.BUSY: 100,
                                                   alto.OUTSTANDING: 1000}))

    def test_partial_interval_is_not_applied_or_repeated(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'perf.log'
            file.write_text('0.5 10 ' + alto.DEMAND + '\n0.5 300 ' + alto.BUSY + '\n')
            reader = alto.IntervalReader()
            self.assertEqual(reader.read(file), [])
            with file.open('a') as f:
                f.write('0.5 2000 ' + alto.OUTSTANDING)
            self.assertEqual(reader.read(file), [])
            with file.open('a') as f:
                f.write('\n')
            self.assertEqual(reader.read(file)[0]['pte_scale'], 0)
            self.assertEqual(reader.read(file), [])


if __name__ == '__main__':
    unittest.main()
