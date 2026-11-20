"""Concatenate files: ``join_files.py OUTPUT INPUT...``.

A list placeholder arrives as ONE argument joined with ``,`` (WORKFLOWS.md 3.8),
so each input argument is split on ``,`` into paths.
"""

import sys

with open(sys.argv[1], "w", encoding="utf-8") as out:
    for arg in sys.argv[2:]:
        for p in arg.split(","):
            if not p:
                continue
            with open(p, encoding="utf-8") as fh:
                out.write(fh.read())
