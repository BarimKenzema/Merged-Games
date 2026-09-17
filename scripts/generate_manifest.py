#!/usr/bin/env python3
import json, glob, os, sys, random

def load_all_puzzles(puzzles_dir):
    all_puzzles = []
    for data_path in sorted(glob.glob(os.path.join(puzzles_dir, "*", "data.json"))):
        with open(data_path) as f:
            d = json.load(f)
        folder = os.path.basename(os.path.dirname(data_path))
        all_puzzles.append({
            "puzzle_id": d["puzzle_id"],
            "source_game": d["source_game"],
            "item_count": len(d["items"]),
            "thumbnail": f"{folder}/{d.get('thumbnail', 'thumb.jpg')}",
            "background": f"{folder}/{d.get('background', 'bg.jpg')}"
        })
    return all_puzzles

def proportional_interleave(puzzles):
    groups = {}
    for p in puzzles:
        groups.setdefault(p["source_game"], []).append(p)
    for g in groups.values():
        random.shuffle(g)
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
    return result

def main():
    puzzles_dir = sys.argv[1]
    manifest_path = sys.argv[2]

    all_puzzles = load_all_puzzles(puzzles_dir)

    # CONFIRMED DESIGN: if a manifest already exists at this path, its
    # existing entries and their ORDER are treated as permanent history and
    # are never reshuffled or reordered - only puzzles not already present
    # get appended, proportionally interleaved AMONG THEMSELVES, at the end.
    # This is what keeps index-based progress tracking (highestUnlockedIndex,
    # lastPlayedIndex) stable across repeated runs against the same directory,
    # and is also what makes "growing" either the base app or the ledger-based
    # bulk pipeline safe over time.
    existing_list = []
    existing_ids = set()
    if os.path.exists(manifest_path):
        with open(manifest_path) as f:
            existing_data = json.load(f)
        existing_list = existing_data.get("puzzles", [])
        existing_ids = set(p["puzzle_id"] for p in existing_list)
        print(f"Found existing manifest with {len(existing_list)} puzzles - preserving their order.")

    new_puzzles = [p for p in all_puzzles if p["puzzle_id"] not in existing_ids]
    interleaved_new = proportional_interleave(new_puzzles)

    result = existing_list + interleaved_new

    with open(manifest_path, 'w') as f:
        json.dump({"puzzles": result}, f, indent=2)

    print(f"Manifest written with {len(result)} total puzzles "
          f"({len(existing_list)} preserved + {len(interleaved_new)} newly appended), "
          f"groups={sorted(set(p['source_game'] for p in all_puzzles))}")

if __name__ == "__main__":
    main()
