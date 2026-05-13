import argparse
import json

parser = argparse.ArgumentParser()
parser.add_argument("--input")
parser.add_argument("--output")
parser.add_argument("--task-type")
args = parser.parse_args()

with open(args.input) as f, open(args.output, "w") as out:
    for line in f:
        for word in line.split():
            out.write(json.dumps([word, 1]) + "\n")
