"""Aggregate timing CSV files exactly as in the PDF."""
import csv,glob,statistics,sys
from collections import defaultdict
from pathlib import Path
groups=defaultdict(list)
folder=Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).resolve().parents[1]/'results'
for p in sorted(folder.glob('bench_*.csv')):
    with p.open() as fp:
        for r in csv.DictReader(fp):
            if int(r['edges'])==8388608 and int(r['block'])==1:
                groups[(float(r['requested_density']),int(r['seed']),r['kernel'])].append(float(r['us_per_step'])/1000)
first={k:statistics.median(v) for k,v in groups.items()}
seeds=sorted({k[1] for k in first})
for d in sorted({k[0] for k in first}):
    print(f'{100*d:g}% active')
    print({m:round(statistics.median(first[d,s,m] for s in seeds),3) for m in ['shuffled','bitmap','ordered','scan','prepare_order']})
    amort=[first[d,s,'prepare_order']/(first[d,s,'shuffled']-first[d,s,'ordered']) for s in seeds if first[d,s,'shuffled']>first[d,s,'ordered']]
    print('Median positive break-even uses',round(statistics.median(amort),2) if amort else None)
