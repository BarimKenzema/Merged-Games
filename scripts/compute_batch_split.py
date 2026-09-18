#!/usr/bin/env python3
"""
Computes how many puzzles to pull from EACH game's download list THIS RUN,
so that over many scheduled runs both pools deplete at (approximately) the
same time - proportional to how many un-converted puzzles currently remain
in each pool, recalculated FRESH every single run. This is deliberately
self-correcting rather than using a fixed ratio baked in once: if downloads
fail, get skipped, or a list simply runs low, the next run automatically
re-balances based on real remaining counts rather than drifting off a
stale assumption.

Usage:
  compute_batch_split.py <total_batch_size> <game1_list> <game1_ledger> <game2_list> <game2_ledger>

Prints (to stdout):
  GAME1_BATCH=<n>
  GAME2_BATCH=<n>
"""
import sys
from filter_new_urls import get_candidates

def main():
    total_batch = int(sys.argv[1])
    g1_list, g1_ledger = sys.argv[2], sys.argv[3]
    g2_list, g2_ledger = sys.argv[4], sys.argv[5]

    g1_remaining = len(get_candidates('game1', g1_list, g1_ledger))
    g2_remaining = len(get_candidates('game2', g2_list, g2_ledger))
    total_remaining = g1_remaining + g2_remaining

    print(f"Remaining before this run: game1={g1_remaining}, game2={g2_remaining}, total={total_remaining}")

    if total_remaining == 0:
        g1_batch, g2_batch = 0, 0
    elif total_remaining <= total_batch:
        # Final stretch: everything left fits in one run - take it all.
        g1_batch, g2_batch = g1_remaining, g2_remaining
    else:
        g1_batch = round(total_batch * g1_remaining / total_remaining)
        g1_batch = min(g1_batch, g1_remaining)
        g2_batch = total_batch - g1_batch
        if g2_batch > g2_remaining:
            g2_batch = g2_remaining
            g1_batch = min(total_batch - g2_batch, g1_remaining)

    print(f"GAME1_BATCH={g1_batch}")
    print(f"GAME2_BATCH={g2_batch}")

if __name__ == '__main__':
    main()
