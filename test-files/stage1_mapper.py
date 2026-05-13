import argparse
import json

parser = argparse.ArgumentParser()
parser.add_argument("--input", required=True)
parser.add_argument("--output", required=True)
parser.add_argument("--task-type")
args = parser.parse_args()

with open(args.input) as f, open(args.output, "w") as out:
    for line in f:
        line = line.strip()

        if not line or line.startswith("#"):
            continue

        parts = line.split()

        # Brightkite checkins format:
        # user_id timestamp latitude longitude location_id
        if len(parts) < 5:
            continue

        location_id = parts[4]

        out.write(json.dumps([location_id, 1]) + "\n")
