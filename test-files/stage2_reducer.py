import argparse
import json

parser = argparse.ArgumentParser()
parser.add_argument("--input", required=True)
parser.add_argument("--output", required=True)
parser.add_argument("--task-type")
args = parser.parse_args()

locations = []

with open(args.input) as f:
    for line in f:
        key, values = json.loads(line)

        # values is a list of [location_id, count] pairs
        for location_id, count in values:
            locations.append((location_id, int(count)))

top_5 = sorted(
    locations,
    key=lambda item: item[1],
    reverse=True
)[:5]

with open(args.output, "w") as out:
    for location_id, count in top_5:
        out.write(f"{location_id} {count}\n")
