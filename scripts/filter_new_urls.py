#!/usr/bin/env python3
"""
Pre-download dedup + batching filter.

Reads a two-column download list (`<local_name_or_id> <url>` per line, same
format both download_list.txt and yolo_download_list.txt already use),
extracts the REAL puzzle id per line (from the URL for game1, from the first
column directly for game2), drops anything already present in the ledger
file, shuffles what's left, optionally truncates to a batch size, and writes
the surviving original lines (unchanged format) to the output path so the
existing curl download loops don't need to change at all.

get_candidates() is factored out so compute_batch_split.py can reuse the
EXACT same dedup logic when computing how many "remaining" puzzles exist
per game - keeping the two scripts always in agreement.
"""
import sys, re, random

def load_ledger(path):
    try:
        with open(path) as f:
            return set(line.strip() for line in f if line.strip())
    except FileNotFoundError:
        return set()

def extract_game1_id(url):
    m = re.search(r'/FindOut/(\d+)/', url)
    return m.group(1) if m else url.strip()

def get_candidates(mode, input_path, ledger_path):
    ledger = load_ledger(ledger_path)
    with open(input_path) as f:
        raw_lines = [l.rstrip('\n') for l in f if l.strip()]

    candidates = []
    seen_this_run = set()
    for line in raw_lines:
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        fname, url = parts
        if mode == 'game1':
            pid = extract_game1_id(url)
        elif mode == 'game2':
            pid = fname
        else:
            raise ValueError(f"Unknown mode: {mode}")
        if pid in ledger or pid in seen_this_run:
            continue
        seen_this_run.add(pid)
        candidates.append(line)
    return candidates

def main():
    if len(sys.argv) < 5:
        print("Usage: filter_new_urls.py <game1|game2> <input_list> <ledger_file> <output_list> [batch_size]", file=sys.stderr)
        sys.exit(1)

    mode = sys.argv[1]
    input_path = sys.argv[2]
    ledger_path = sys.argv[3]
    output_path = sys.argv[4]
    batch_size = int(sys.argv[5]) if len(sys.argv) > 5 else None

    candidates = get_candidates(mode, input_path, ledger_path)
    total_new = len(candidates)
    random.shuffle(candidates)
    selected = candidates[:batch_size] if batch_size is not None else candidates

    with open(output_path, 'w') as f:
        for line in selected:
            f.write(line + '\n')

    print(f"[{mode}] not-yet-converted: {total_new}, selected this run: {len(selected)}")

if __name__ == '__main__':
    main()
