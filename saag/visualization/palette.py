"""
Visualization Colour Palette

The single source of truth for every colour the dashboard renders — chart
series, Cytoscape nodes, D3 matrix cells, badges and hierarchy dots all read
from here so they cannot drift apart.

Criticality ramp is accessibility-tested; RM colours carry dimension
semantics (R=purple structural authority, M=teal maintainability,
FT=indigo propagation reach, A=coral operational risk — FT and A are
Reliability's sub-characteristics, not peer dimensions).
"""
from typing import Dict

from saag.analysis.weight_calculator import QualityWeights

# ── Criticality levels ────────────────────────────────────────────────────────
CRITICALITY_COLORS: Dict[str, str] = {
    "CRITICAL": "#A32D2D",   # red-800
    "HIGH":     "#854F0B",   # amber-800
    "MEDIUM":   "#185FA5",   # blue-800
    "LOW":      "#3B6D11",   # green-800
    "MINIMAL":  "#5F5E5A",   # gray-800
}

# Muted background / saturated foreground pairs for criticality badges.
CRITICALITY_BADGE_COLORS: Dict[str, tuple] = {
    "CRITICAL": ("#FCEBEB", "#791F1F"),
    "HIGH":     ("#FAEEDA", "#633806"),
    "MEDIUM":   ("#E6F1FB", "#0C447C"),
    "LOW":      ("#EAF3DE", "#27500A"),
    "MINIMAL":  ("#F1EFE8", "#444441"),
}

DEFAULT_COLOR = CRITICALITY_COLORS["MINIMAL"]

# ── RM quality dimensions ───────────────────────────────────────────────────
RM_COLORS: Dict[str, str] = {
    "reliability":     "#534AB7",   # purple
    "maintainability": "#0F6E56",   # teal
    "fault_tolerance": "#3B4FA1",   # indigo (Reliability sub-characteristic)
    "availability":    "#993C1D",   # coral (Reliability sub-characteristic)
}

# Composite dimension weights — bar segments are scaled by these so a stacked
# RM bar sums to Q(v). Reads from QualityWeights rather than restating the
# numbers, which is what let this dict and the real weights drift apart before.
COMPOSITE_WEIGHTS: Dict[str, float] = {
    "reliability":     QualityWeights().q_reliability,
    "maintainability": QualityWeights().q_maintainability,
}

# Effective weight of each *reported* score (fault_tolerance, availability,
# maintainability) toward Q(v), expanding R = r_alpha*FT + (1-r_alpha)*A so a
# stacked bar over these three still sums to Q(v). Reliability itself is not
# a bar segment here — it is the sum of the FT and A segments.
_qw = QualityWeights()
EFFECTIVE_WEIGHTS: Dict[str, float] = {
    "fault_tolerance": _qw.q_reliability * _qw.r_alpha,
    "availability":    _qw.q_reliability * (1.0 - _qw.r_alpha),
    "maintainability": _qw.q_maintainability,
}
del _qw

# ── Component types ───────────────────────────────────────────────────────────
TYPE_COLORS: Dict[str, str] = {
    "Application": "#534AB7",
    "Broker":      "#0F6E56",
    "Node":        "#185FA5",
    "Topic":       "#993C1D",
    "Library":     "#3B6D11",
    "NetworkDevice": "#5F5E5A",
}

# ── Component shapes for Cytoscape network graph ──────────────────────────────
TYPE_SHAPES: Dict[str, str] = {
    "Application": "round-rectangle",
    "Broker":      "diamond",
    "Topic":       "ellipse",
    "Library":     "hexagon",
    "Node":        "barrel",
    "NetworkDevice": "triangle",
}


# ── MIL-STD-498 hierarchy levels: (background, accent) ─────────────────────────
HIERARCHY_COLORS: Dict[str, tuple] = {
    "CSS":  ("#EEEDFE", "#534AB7"),
    "CSCI": ("#E1F5EE", "#0F6E56"),
    "CSC":  ("#E6F1FB", "#185FA5"),
    "CSU":  ("#F1EFE8", "#5F5E5A"),
}

# ── Accents used outside the semantic ramps ───────────────────────────────────
BRAND_PURPLE = "#534AB7"     # primary accent: matrix cells, QoS-enriched bars
NEUTRAL_GREY = "#B4B2A9"     # baseline / comparison series

# Qualitative fallback ramp for charts with arbitrary category counts.
CATEGORICAL_PALETTE = [
    "#534AB7", "#0F6E56", "#993C1D", "#993556",
    "#185FA5", "#3B6D11", "#854F0B",
]


# ── Stakeholder Roles (Triage Bridge) ─────────────────────────────────────────
ROLE_BADGE_COLORS: Dict[str, tuple] = {
    "DevOps / SRE":     ("#E6F1FB", "#0C447C"),  # blue operational
    "DevOps":           ("#E6F1FB", "#0C447C"),
    "SRE":              ("#E6F1FB", "#0C447C"),
    "Architect":        ("#EEEDFE", "#534AB7"),  # purple architectural
    "System Architect": ("#EEEDFE", "#534AB7"),
    "Developer":        ("#E1F5EE", "#0F6E56"),  # teal code/maintainability
    "Software Developer": ("#E1F5EE", "#0F6E56"),
}


def criticality_badge_css() -> str:
    """Render the `.badge-<level>` rules from CRITICALITY_BADGE_COLORS."""
    return "\n".join(
        f"    .badge-{level.lower():<9} {{ background: {bg}; color: {fg}; }}"
        for level, (bg, fg) in CRITICALITY_BADGE_COLORS.items()
    )

