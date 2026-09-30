"""保护时钟精度、半开区间、线程权重和符号边界；不读取主机地址。"""
from pathlib import Path
import tempfile
import unittest
from analyze_buffer_reuse_kernel import KernelSymbols,parse_raw,contains,summarize

class KernelReuseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        p=Path(self.tmp.name)/'symbols'
        p.write_text('1000 T _stext\n1010 T native_queued_spin_lock_slowpath\n'
                     '1020 t folio_batch_move_lru\n1030 t do_anonymous_page\n'
                     '1040 T copy_page\n1040 T copy_page_alias\n1050 t __pte_offset_map_lock\n'
                     '1060 t __rmqueue_pcplist\n2000 T _etext\n'
                     '3000 t module_function [module]\n')
        self.symbols=KernelSymbols(p)
    def tearDown(self):self.tmp.cleanup()
    def test_ns_and_thread_conservation(self):
        raw='10/11 1031831.123456789: 20\n\t1012\n\t1022\n\t1032\n\n10/12 1031831.123456790: 80\n\t1042\n'
        rows=list(parse_raw(raw,self.symbols))
        self.assertEqual(rows[0]['time_ns'],1031831123456789)
        whole=summarize(rows,2);worker=summarize(rows[:1],2)
        self.assertEqual(whole['period'],100)
        self.assertEqual(worker['categories']['lru_spin']['period'],20)
        self.assertEqual(worker['categories']['anon_lru_spin']['period'],20)
        self.assertEqual(whole['categories']['copy_page']['period'],80)
        self.assertNotIn('1012',str(whole))
    def test_half_open_intervals(self):
        iv=[(10,20),(30,40)];starts=[10,30]
        self.assertEqual([contains(iv,starts,x) for x in [9,10,19,20,29,30,40]],
                         [False,True,True,False,False,True,False])
        self.assertFalse(contains([],[],30))
    def test_unknown_and_alias_boundaries(self):
        self.assertEqual(self.symbols.lookup(0x1042),['copy_page','copy_page_alias'])
        for ip in [0,0xfff,0x2000,0x3000,0xffff]:self.assertEqual(self.symbols.lookup(ip),[])
        row=list(parse_raw('1/2 1.1: 40\n\t3000\n',self.symbols))
        self.assertEqual(row[0]['time_ns'],1100000000)
        self.assertEqual(summarize(row,1)['categories']['unresolved']['period'],40)
    def test_malformed_and_lost_fail(self):
        for raw in ['lost 50 events','1/2 1.1: 40\n','1/2 1.1: 40\n\tnot_hex\n']:
            with self.assertRaises(ValueError):list(parse_raw(raw,self.symbols))
    def test_empty_group(self):
        group=summarize([],100)
        self.assertEqual(group['period'],0)
        self.assertIsNone(group['categories']['lru_spin']['percent_of_group'])
    def test_pte_and_allocator_not_lru(self):
        rows=list(parse_raw('1/2 1.1: 40\n\t1012\n\t1052\n1/2 1.2: 60\n\t1012\n\t1062\n',self.symbols))
        categories=summarize(rows,1)['categories']
        self.assertEqual(categories['pte_map_spin']['period'],40)
        self.assertEqual(categories['page_allocator_spin']['period'],60)
        self.assertEqual(categories['spin']['period'],100)
        self.assertEqual(categories['lru_spin']['period'],0)

if __name__=='__main__':unittest.main(verbosity=2)
