import argparse
import json

parser = argparse.ArgumentParser()
parser.add_argument("--input")
parser.add_argument("--output")
parser.add_argument("--task-type")
args = parser.parse_args()

counts = {}

with open(args.input) as f:
    for line in f:
        key, values = json.loads(line)
        counts[key] = counts.get(key, 0) + sum(values)

with open(args.output, "w") as out:
    for key in sorted(counts):
        out.write(f"{key} {counts[key]}\n")
