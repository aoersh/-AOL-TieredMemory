#!/usr/bin/env python3
"""编译可选 C 计时包装器并保存实际编译命令、版本及哈希。"""
import hashlib,json,os,subprocess,tempfile
from pathlib import Path
root=Path(__file__).resolve().parents[2]
source=root/'research/cpu_training/migration_meter.c'
output=root/'.deps/training-native';output.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='meter-build-',dir=output) as temporary:
    binary=Path(temporary)/'libmigration_meter.so'
    command=['gcc','-O2','-Wall','-Wextra','-Werror','-shared','-fPIC',str(source),'-o',str(binary)]
    subprocess.run(command,check=True)
    metadata=dict(command=command,compiler=subprocess.check_output(['gcc','--version'],text=True).splitlines()[0],source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),sha256=hashlib.sha256(binary.read_bytes()).hexdigest())
    os.replace(binary,output/'libmigration_meter.so')
    (output/'meter-build.json').write_text(json.dumps(metadata,indent=2)+'\n')
print(output/'libmigration_meter.so')
