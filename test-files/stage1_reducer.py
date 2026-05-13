import argparse
import json

parser = argparse.ArgumentParser()
parser.add_argument("--input", required=True)
parser.add_argument("--output", required=True)
parser.add_argument("--task-type")
args = parser.parse_args()

location_counts = {}

with open(args.input) as f:
    for line in f:
        key, values = json.loads(line)
        location_counts[key] = location_counts.get(key, 0) + sum(values)

top_5_locations = sorted(
    location_counts.items(),
    key=lambda item: item[1],
    reverse=True
)[:5]

with open(args.output, "w") as out:
    for location_id, count in top_5_locations:
        out.write(f"{location_id} {count}\n")
