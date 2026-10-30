"""Loads the business settings from config/rules.yaml.

Everything a non-technical person might want to change lives in that YAML
file. This module only reads/writes it and converts % values to fractions.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "rules.yaml"


def load(path: Path | str = CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


_LINE = re.compile(r"^(?P<indent>\s*)(?P<key>[^#:\s][^:]*):(?P<sp>\s*)(?P<val>[^#]*?)(?P<rest>\s*(#.*)?)$")


def save(cfg: dict, path: Path | str = CONFIG_PATH) -> None:
    """Write changed values back into the YAML file, keeping every comment
    (the comments are the instructions for non-technical editors)."""
    path = Path(path)
    out, stack = [], []  # stack of (indent, key)
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _LINE.match(line)
        if not m or line.lstrip().startswith("#"):
            out.append(line)
            continue
        indent = len(m["indent"])
        while stack and stack[-1][0] >= indent:
            stack.pop()
        key = m["key"].strip()
        stack.append((indent, key))
        if m["val"].strip() == "":
            out.append(line)
            continue
        node = cfg
        for _, k in stack:
            node = node.get(k) if isinstance(node, dict) else None
        if node is None or isinstance(node, dict):
            out.append(line)
            continue
        val = yaml.safe_dump(node, default_flow_style=True).strip().removesuffix("\n...").removesuffix("...").strip()
        out.append(f"{m['indent']}{m['key']}:{m['sp'] or ' '}{val}{m['rest']}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    assert load(path) == cfg, "config save round-trip failed"


def rto_cost(cfg: dict) -> float:
    """Total Rs lost on one RTO, from the money settings."""
    m = cfg["money"]
    damage = m["damaged_return_rate"] / 100 * m["avg_product_cost"]
    return m["forward_shipping"] + m["return_shipping"] + m["packaging_handling"] + damage
