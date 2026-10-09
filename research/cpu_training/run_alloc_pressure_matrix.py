#!/usr/bin/env python3
"""兼容入口：使用已修正的closure pressure矩阵；先--diagnostic，再--diagnostic-reference。"""
import sys
from run_closure_matrix import main
if __name__=='__main__':
    sys.argv.insert(1,'pressure')
    main()
