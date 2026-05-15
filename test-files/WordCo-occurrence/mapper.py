# Emits focus-word/neighbor pairs found within a small sliding window.

import argparse
import json
import re

WINDOW_SIZE = 2


def tokenize(line):
    return re.findall(r"[a-zA-Z0-9]+", line.lower())


parser = argparse.ArgumentParser()
parser.add_argument("--input")
parser.add_argument("--output")
parser.add_argument("--task-type")
args = parser.parse_args()

with open(args.input) as f, open(args.output, "w") as out:
    for line in f:
        words = tokenize(line)

        for i, word in enumerate(words):
            start = max(0, i - WINDOW_SIZE)
            end = min(len(words), i + WINDOW_SIZE + 1)

            for j in range(start, end):
                if i == j:
                    continue

                neighbor = words[j]
                out.write(json.dumps([f"{word},{neighbor}", 1]) + "\n")
