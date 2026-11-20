"""The state table in docs/addons/slurm.md must equal slurm.STATE_MAP."""
import re
from pathlib import Path

from stradler.ext import slurm

DOC = Path(__file__).resolve().parents[2] / "docs" / "addons" / "slurm.md"


def _doc_map():
    text = DOC.read_text()
    section = text.split("### State mapping", 1)[1].split("\n## ", 1)[0]
    out = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 2 or not line.startswith("|"):
            continue
        proto = re.fullmatch(r"`(pending|running|completed|failed|cancelled)`", cells[1])
        if not proto or ";" in cells[0] or "(" in cells[0]:
            if proto and cells[0].startswith("CANCELLED"):
                out["CANCELLED"] = proto.group(1)
                out["REVOKED"] = proto.group(1)
            continue
        for name in cells[0].split(","):
            out[name.strip()] = proto.group(1)
    return out


def test_doc_state_table_equals_state_map():
    assert _doc_map() == slurm.STATE_MAP
