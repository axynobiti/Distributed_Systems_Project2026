# Sums grouped co-occurrence counts for each focus-word/neighbor pair.

import argparse
import json

parser = argparse.ArgumentParser()
parser.add_argument("--input")
parser.add_argument("--output")
parser.add_argument("--task-type")
args = parser.parse_args()

with open(args.input) as f, open(args.output, "w") as out:
    for line in f:
        line = line.strip()

        if not line:
            continue

        pair, counts = json.loads(line)
        out.write(f"{pair}\t{sum(counts)}\n")
