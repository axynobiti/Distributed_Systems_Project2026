import argparse
import json

parser = argparse.ArgumentParser()
parser.add_argument("--input", required=True)
parser.add_argument("--output", required=True)
parser.add_argument("--task-type")
args = parser.parse_args()

with open(args.input) as f, open(args.output, "w") as out:
    for line in f:
        parts = line.strip().split()

        if len(parts) != 2:
            continue

        location_id = parts[0]
        count = int(parts[1])

        # Send every location count to the same key.
        # This lets the reducer compare all locations together.
        out.write(json.dumps(["global_top5", [location_id, count]]) + "\n")
