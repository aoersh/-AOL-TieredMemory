#!/usr/bin/env python3
"""Plot independent-run means and exploratory paired confidence intervals."""
import argparse
import csv
import json
import os
from pathlib import Path
import sys

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('results',type=Path)
a=p.parse_args()
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'.deps/python'))
os.environ['MPLCONFIGDIR']=str(a.results/'mpl-cache')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

rows=list(csv.DictReader((a.results/'process-means.csv').open()))
summary=json.loads((a.results/'summary.json').read_text())
fig,axes=plt.subplots(1,3,figsize=(12,4),layout='constrained')
modes=['dram','direct','sync','async']
for ax,shape in zip(axes[:2],('small','large')):
 for index,mode in enumerate(modes):
  values=[float(r['mean_step_ms']) for r in rows if r['shape']==shape and r['mode']==mode]
  ax.scatter([index]*len(values),values,s=26,alpha=.8)
  ax.plot([index-.18,index+.18],[summary[shape][mode]['mean_step_ms']]*2,color='black')
 ax.set_xticks(range(4),['Managed\nDRAM','Direct\nCXL','Sync\nmigrate','Eager\nasync'])
 ax.set_ylabel('Mean step time per process (ms)')
 ax.set_title(f'Transformer {shape}; n=5 processes')
 ax.grid(axis='y',alpha=.25)
for i,shape in enumerate(('small','large')):
 stat=summary[shape]['paired_direct_minus_async']
 mean=stat['mean_ms'];lo,hi=stat['ci95_ms']
 axes[2].errorbar(mean,i,xerr=[[mean-lo],[hi-mean]],fmt='o',capsize=5)
axes[2].axvline(0,color='gray',linestyle='--')
axes[2].set_yticks([0,1],['small','large'])
axes[2].set_xlabel('Direct minus async step time (ms)\nPositive favors async; paired 95% t CI')
axes[2].set_title('Exploratory, ample-capacity pilot')
fig.savefig(a.results/'path-comparison.png',dpi=180)
fig.savefig(a.results/'path-comparison.pdf')
