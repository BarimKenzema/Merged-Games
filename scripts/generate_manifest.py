#!/usr/bin/env python3
import json, glob, os, sys

puzzles_dir = sys.argv[1]
manifest_path = sys.argv[2]

puzzles = []
for data_path in sorted(glob.glob(os.path.join(puzzles_dir, "*", "data.json"))):
    with open(data_path) as f:
        d = json.load(f)
    puzzles.append({
        "puzzle_id": d["puzzle_id"],
        "source_game": d["source_game"],
        "item_count": len(d["items"])
    })

with open(manifest_path, 'w') as f:
    json.dump({"puzzles": puzzles}, f, indent=2)

print(f"Manifest written with {len(puzzles)} puzzles")
