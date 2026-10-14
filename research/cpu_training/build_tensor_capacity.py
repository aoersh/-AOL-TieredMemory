#!/usr/bin/env python3
"""构建独立C10 allocator；新目录保留源码、ABI和命令，不修改旧库。"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from observe_saved_tensors import torch, ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).with_name('tensor_capacity_allocator.cpp')
    (args.output/source.name).write_bytes(source.read_bytes())
    package = ROOT/'.deps/training-python/torch'
    command = ['g++', '-std=c++17', '-O2', '-shared', '-fPIC', '-pthread',
               '-D_GLIBCXX_USE_CXX11_ABI='+str(int(torch._C._GLIBCXX_USE_CXX11_ABI)),
               '-I'+str(package/'include'), str(source), '-L'+str(package/'lib'),
               '-Wl,-rpath,'+str(package/'lib'), '-lc10', '-lnuma',
               '-o', str(args.output/'libtensor_capacity.so')]
    subprocess.run(command, check=True)
    manifest = dict(torch=torch.__version__, abi=int(torch._C._GLIBCXX_USE_CXX11_ABI),
                    command=command, source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                    library_sha256=hashlib.sha256((args.output/'libtensor_capacity.so').read_bytes()).hexdigest())
    (args.output/'build.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print('PASS', args.output)


if __name__ == '__main__':
    main()
