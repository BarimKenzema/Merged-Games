#!/usr/bin/env python3
import json, glob, os, sys, random

puzzles_dir = sys.argv[1]
manifest_path = sys.argv[2]

all_puzzles = []
for data_path in sorted(glob.glob(os.path.join(puzzles_dir, "*", "data.json"))):
    with open(data_path) as f:
        d = json.load(f)
    folder = os.path.basename(os.path.dirname(data_path))
    all_puzzles.append({
        "puzzle_id": d["puzzle_id"],
        "source_game": d["source_game"],
        "item_count": len(d["items"]),
        "thumbnail": f"{folder}/{d.get('thumbnail', 'thumb.jpg')}"
    })

groups = {}
for p in all_puzzles:
    groups.setdefault(p["source_game"], []).append(p)

for g in groups.values():
    random.shuffle(g)

# Proportional weighted interleave: always pick from whichever group
# has been used the smallest fraction of its total so far. This spreads
# smaller catalogs evenly across the whole sequence instead of clumping.
result = []
indices = {k: 0 for k in groups}
totals = {k: len(v) for k, v in groups.items()}
remaining = sum(totals.values())

while remaining > 0:
    best_key, best_ratio = None, None
    for k in groups:
        if indices[k] >= totals[k]:
            continue
        ratio = indices[k] / totals[k] if totals[k] > 0 else 1
        if best_ratio is None or ratio < best_ratio:
            best_ratio, best_key = ratio, k
    result.append(groups[best_key][indices[best_key]])
    indices[best_key] += 1
    remaining -= 1

with open(manifest_path, 'w') as f:
    json.dump({"puzzles": result}, f, indent=2)

print(f"Manifest written with {len(result)} puzzles, groups={list(totals.keys())}")
