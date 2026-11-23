"""Generate Mermaid architecture diagrams from an Architecture.

Diagrams are built from the structured result, never from LLM text, so they
always match the priced components.
"""

from __future__ import annotations

from .models import Architecture

_ICON = {
    "api": "🔀", "load_balancer": "⚖️", "compute": "⚙️", "cache": "⚡",
    "database": "🗄️", "storage": "📦", "cdn": "🌐",
}


def _node(id_: str, label: str, shape: str = "rect") -> str:
    label = label.replace('"', "'")
    return {"rect": f'{id_}["{label}"]', "db": f'{id_}[("{label}")]',
            "round": f'{id_}(["{label}"])'}[shape]


def to_mermaid(arch: Architecture) -> str:
    by_role: dict[str, list] = {}
    for c in arch.components:
        by_role.setdefault(c.role, []).append(c)

    lines = ["flowchart LR"]
    lines.append("  " + _node("users", "👥 Users", "round"))

    def label(c):
        return f"{_ICON.get(c.role, '')} {c.service}<br/><small>{c.config}</small>"

    region = "Region A + Region B (active-active)" if arch.id.endswith("-mr") else "Single region, multi-zone"
    lines.append(f'  subgraph region["{arch.cloud.label} · {region}"]')
    lines.append("    direction LR")
    chain: list[str] = []
    for role in ("api", "load_balancer", "compute"):
        for i, c in enumerate(by_role.get(role, [])):
            nid = f"{role}{i}"
            lines.append("    " + _node(nid, label(c)))
            chain.append(nid)
    data_nodes = []
    for role in ("cache", "database", "storage"):
        for i, c in enumerate(by_role.get(role, [])):
            nid = f"{role}{i}"
            lines.append("    " + _node(nid, label(c), "db" if role != "cache" else "rect"))
            data_nodes.append((role, nid))
    lines.append("  end")

    entry = "users"
    if "cdn" in by_role:
        c = by_role["cdn"][0]
        lines.append("  " + _node("cdn0", label(c)))
        lines.append("  users --> cdn0")
        entry = "cdn0"
    if chain:
        lines.append(f"  {entry} --> {chain[0]}")
        for a, b in zip(chain, chain[1:]):
            lines.append(f"  {a} --> {b}")
        compute = chain[-1]
        for role, nid in data_nodes:
            style = "-.->" if role == "storage" else "-->"
            lines.append(f"  {compute} {style} {nid}")
    if "cache" in by_role and "database" in by_role:
        lines.append("  cache0 -. miss .-> database0")

    lines.append("  classDef data fill:#eef6ff,stroke:#5b8def")
    data_ids = ",".join(n for _, n in data_nodes)
    if data_ids:
        lines.append(f"  class {data_ids} data")
    return "\n".join(lines)
