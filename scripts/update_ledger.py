#!/usr/bin/env python3
"""
Registers puzzle IDs from an already-converted puzzles directory into a
ledger file, WITHOUT re-running conversion or consulting the ledger for
skip-checks.

This exists specifically for the base-APK workflow: that workflow
deliberately converts its 16 bundled puzzles WITHOUT passing a ledger to
convert_game1.py/convert_game2.py (so rebuilding the base APK - e.g. after a
code fix - never accidentally skips its own already-registered puzzles as
"duplicates"). This script is then run separately, exactly once, immediately
after the very first base selection is created, purely so future bulk
data-pack runs know these 16 are already spoken for and never re-select them.

Usage: update_ledger.py <game1|game2> <puzzles_dir> <ledger_file>
"""
import sys, os, glob

game_prefix, puzzles_dir, ledger_path = sys.argv[1], sys.argv[2], sys.argv[3]

existing = set()
if os.path.exists(ledger_path):
    with open(ledger_path) as f:
        existing = set(line.strip() for line in f if line.strip())

added = []
for data_path in glob.glob(os.path.join(puzzles_dir, f"{game_prefix}_*", "data.json")):
    folder_name = os.path.basename(os.path.dirname(data_path))
    raw_id = folder_name[len(game_prefix) + 1:]
    if raw_id and raw_id not in existing:
        existing.add(raw_id)
        added.append(raw_id)

if added:
    with open(ledger_path, 'a') as f:
        for pid in added:
            f.write(pid + '\n')

print(f"Registered {len(added)} ids into {ledger_path}")
