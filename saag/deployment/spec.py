"""
Deployment spec: the infrastructure facts a topology does not carry.

The topology says which components exist and how they communicate. The spec
says what each Node can hold (cpu, memory), how likely it is to fail, which
failure domain it shares with others, how far apart Nodes are (latency), what
each Application and Broker demands, how many replicas it needs and where it is
allowed to run. Keeping these out of the topology leaves the topology schema,
its Neo4j persistence and the byte-identical scenario corpus untouched.

Example (YAML or JSON)::

    version: 1
    defaults:
      node:   {cpu: 16, mem_mb: 32768, p: 0.02}
      domain: {p: 0.005}
      app:    {cpu: 1.0, mem_mb: 512}        # replicas: 2 if hotstandby else 1
      broker: {cpu: 2.0, mem_mb: 2048}
    nodes:   {N0: {cpu: 32, domain: rack-0}}
    domains: {rack-0: {p: 0.004}}
    latency: {per_hop_ms: 0.1, intra_domain_ms: 0.5, inter_domain_ms: 5.0,
              matrix_ms: {N0: {N5: 1.7}}}
    apps:    {A1: {cpu: 2.0, replicas: 2, forbid: [N7]}, A9: {pin: [N0, N3]}}
    brokers: {B0: {pin: [N2]}}
    constraints: {anti_affinity: domain, use_all_hosts: false, max_utilization: 1.0}
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from saag.core.network import Fabric

SPEC_VERSION = 1

SECTIONS = ("version", "description", "defaults", "nodes", "domains", "latency", "apps", "brokers", "constraints")
NODE_KEYS = ("cpu", "mem_mb", "p", "domain")
DOMAIN_KEYS = ("p",)
COMPONENT_KEYS = ("cpu", "mem_mb", "replicas", "pin", "forbid")
LATENCY_KEYS = ("per_hop_ms", "intra_domain_ms", "inter_domain_ms", "matrix_ms")
CONSTRAINT_KEYS = ("anti_affinity", "use_all_hosts", "max_utilization")

DEFAULT_LATENCY = {"per_hop_ms": 0.1, "intra_domain_ms": 0.5, "inter_domain_ms": 5.0}
DEFAULT_CONSTRAINTS = {"anti_affinity": "domain", "use_all_hosts": False, "max_utilization": 1.0}
DEFAULT_COMPONENT = {
    "app": {"cpu": 1.0, "mem_mb": 512.0},
    "broker": {"cpu": 2.0, "mem_mb": 2048.0},
}
DEFAULT_DOMAIN = {"p": 0.0}


class DeploymentSpecError(ValueError):
    """A spec that cannot be used; ``problems`` lists every issue found."""

    def __init__(self, problems: List[str]):
        self.problems = list(problems)
        super().__init__("invalid deployment spec:\n  - " + "\n  - ".join(self.problems))


@dataclass
class DeploymentSpec:
    """A parsed spec, section by section, before it is checked against a topology."""
    defaults: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    nodes: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    domains: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    latency: Dict[str, Any] = field(default_factory=dict)
    apps: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    brokers: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    constraints: Dict[str, Any] = field(default_factory=dict)
    source: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"version": SPEC_VERSION, "defaults": self.defaults, "nodes": self.nodes,
                "domains": self.domains, "latency": self.latency, "apps": self.apps,
                "brokers": self.brokers, "constraints": self.constraints}


@dataclass
class BoundSpec:
    """A spec resolved against one topology: every value explicit, every id real."""
    hosts: List[str]
    host_cpu: Dict[str, float]
    host_mem: Dict[str, float]
    host_p: Dict[str, float]
    domain: Dict[str, str]                       # host -> failure domain
    domain_p: Dict[str, float]
    latency: Dict[str, Dict[str, float]]         # host -> host -> ms
    latency_source: str
    cpu: Dict[str, float]                        # component -> demand per copy
    mem_mb: Dict[str, float]
    replicas: Dict[str, int]
    allowed: Dict[str, List[str]]                # component -> hosts it may run on
    anti_affinity: str
    use_all_hosts: bool
    max_utilization: float

    def summary(self) -> Dict[str, Any]:
        return {
            "hosts": len(self.hosts),
            "domains": len(set(self.domain.values())),
            "components": len(self.replicas),
            "replicated": sorted(c for c, r in self.replicas.items() if r > 1),
            "pinned": sorted(c for c, hs in self.allowed.items() if len(hs) < len(self.hosts)),
            "latency_source": self.latency_source,
            "anti_affinity": self.anti_affinity,
            "use_all_hosts": self.use_all_hosts,
            "max_utilization": self.max_utilization,
        }


# ── Loading ──────────────────────────────────────────────────────────────────

def load_spec(src: Union[str, Path, Dict[str, Any], DeploymentSpec]) -> DeploymentSpec:
    """Parse a spec from a path (.json, .yaml, .yml), a dict, or pass one through."""
    if isinstance(src, DeploymentSpec):
        return src
    source = None
    if isinstance(src, (str, Path)):
        path = Path(src)
        source = str(path)
        text = path.read_text()
        if path.suffix.lower() in (".yaml", ".yml"):
            try:
                import yaml
            except ImportError as exc:
                raise DeploymentSpecError(
                    [f"{path}: YAML specs need PyYAML (pip install pyyaml), or use JSON"]) from exc
            raw = yaml.safe_load(text)
        else:
            raw = json.loads(text)
    else:
        raw = src
    if not isinstance(raw, dict):
        raise DeploymentSpecError(["the spec must be a mapping"])

    problems = [f"unknown section '{k}'" for k in sorted(raw) if k not in SECTIONS]
    version = raw.get("version", SPEC_VERSION)
    if version != SPEC_VERSION:
        problems.append(f"unsupported version {version!r} (expected {SPEC_VERSION})")

    def section(name: str) -> Dict[str, Any]:
        value = raw.get(name) or {}
        if not isinstance(value, dict):
            problems.append(f"'{name}' must be a mapping")
            return {}
        return value

    spec = DeploymentSpec(
        defaults=section("defaults"), nodes=section("nodes"), domains=section("domains"),
        latency=section("latency"), apps=section("apps"), brokers=section("brokers"),
        constraints=section("constraints"), source=source,
    )
    _check_keys(spec, problems)
    if problems:
        raise DeploymentSpecError(problems)
    return spec


def _check_keys(spec: DeploymentSpec, problems: List[str]) -> None:
    def keys(where: str, entry: Any, allowed: Tuple[str, ...]) -> None:
        if not isinstance(entry, dict):
            problems.append(f"{where} must be a mapping")
            return
        problems.extend(f"{where}: unknown key '{k}'" for k in sorted(entry) if k not in allowed)

    for kind, allowed in (("node", NODE_KEYS), ("domain", DOMAIN_KEYS),
                          ("app", COMPONENT_KEYS), ("broker", COMPONENT_KEYS)):
        if kind in spec.defaults:
            keys(f"defaults.{kind}", spec.defaults[kind], allowed)
    problems.extend(f"defaults: unknown key '{k}'" for k in sorted(spec.defaults)
                    if k not in ("node", "domain", "app", "broker"))
    for name, entries, allowed in (("nodes", spec.nodes, NODE_KEYS), ("domains", spec.domains, DOMAIN_KEYS),
                                   ("apps", spec.apps, COMPONENT_KEYS), ("brokers", spec.brokers, COMPONENT_KEYS)):
        for eid in sorted(entries):
            keys(f"{name}.{eid}", entries[eid], allowed)
    keys("latency", spec.latency, LATENCY_KEYS)
    keys("constraints", spec.constraints, CONSTRAINT_KEYS)


# ── Binding to a topology ────────────────────────────────────────────────────

def bind_spec(spec: Union[DeploymentSpec, Dict[str, Any], str, Path], topology: Dict[str, Any]) -> BoundSpec:
    """Check *spec* against *topology*, apply defaults and resolve latency.

    Collects every problem before raising, so one run reports them all.
    """
    spec = load_spec(spec)
    problems: List[str] = []
    hosts = sorted(n["id"] for n in topology.get("nodes", []))
    apps = {a["id"]: a for a in topology.get("applications", [])}
    brokers = sorted(b["id"] for b in topology.get("brokers", []))
    if not hosts:
        problems.append("the topology has no Nodes to deploy onto")

    for name, entries, known in (("nodes", spec.nodes, set(hosts)), ("apps", spec.apps, set(apps)),
                                 ("brokers", spec.brokers, set(brokers))):
        problems.extend(f"{name}.{eid}: not in the topology" for eid in sorted(entries) if eid not in known)

    # Nodes.
    node_default = spec.defaults.get("node")
    host_cpu, host_mem, host_p, domain = {}, {}, {}, {}
    for h in hosts:
        entry = {**(node_default or {}), **spec.nodes.get(h, {})}
        if node_default is None and h not in spec.nodes:
            problems.append(f"node {h}: not in the spec and no defaults.node given")
            continue
        for key in ("cpu", "mem_mb", "p"):
            if key not in entry:
                problems.append(f"node {h}: missing '{key}'")
        host_cpu[h] = _positive(entry.get("cpu", 1.0), f"node {h}: cpu", problems)
        host_mem[h] = _positive(entry.get("mem_mb", 1.0), f"node {h}: mem_mb", problems)
        host_p[h] = _probability(entry.get("p", 0.0), f"node {h}: p", problems)
        domain[h] = str(entry.get("domain") or h)

    # Domains: a Node without one is its own singleton domain.
    domain_ids = sorted(set(domain.values()))
    problems.extend(f"domains.{d}: no Node is in this domain" for d in sorted(spec.domains) if d not in domain_ids)
    problems.extend(f"domain '{d}' has the same name as a Node" for d in domain_ids
                    if d in host_cpu and [h for h in hosts if domain.get(h) == d] != [d])
    domain_default = {**DEFAULT_DOMAIN, **spec.defaults.get("domain", {})}
    domain_p = {d: _probability({**domain_default, **spec.domains.get(d, {})}["p"], f"domain {d}: p", problems)
                for d in domain_ids}

    # Components.
    cpu, mem, replicas, allowed = {}, {}, {}, {}
    host_set = set(hosts)
    components = [(a, "app", spec.apps, 2 if apps[a].get("hotstandby") else 1) for a in sorted(apps)]
    components += [(b, "broker", spec.brokers, 1) for b in brokers]
    for c, kind, entries, default_r in components:
        entry = {**DEFAULT_COMPONENT[kind], **spec.defaults.get(kind, {}), **entries.get(c, {})}
        cpu[c] = _positive(entry["cpu"], f"{kind} {c}: cpu", problems)
        mem[c] = _positive(entry["mem_mb"], f"{kind} {c}: mem_mb", problems)
        r = entry.get("replicas", default_r)
        if not isinstance(r, int) or isinstance(r, bool) or r < 1:
            problems.append(f"{kind} {c}: replicas must be an integer >= 1, got {r!r}")
            r = 1
        replicas[c] = r
        pin = _host_list(entry.get("pin"), f"{kind} {c}: pin", host_set, problems)
        forbid = set(_host_list(entry.get("forbid"), f"{kind} {c}: forbid", host_set, problems) or [])
        ok = [h for h in (pin if pin is not None else hosts) if h not in forbid]
        if not ok:
            problems.append(f"{kind} {c}: pin and forbid leave no allowed Node")
        allowed[c] = sorted(set(ok))

    # Constraints.
    constraints = {**DEFAULT_CONSTRAINTS, **spec.constraints}
    if constraints["anti_affinity"] not in ("domain", "host"):
        problems.append(f"constraints.anti_affinity must be 'domain' or 'host', got {constraints['anti_affinity']!r}")
    if not isinstance(constraints["use_all_hosts"], bool):
        problems.append("constraints.use_all_hosts must be true or false")
    mu = constraints["max_utilization"]
    if not isinstance(mu, (int, float)) or isinstance(mu, bool) or not 0 < mu <= 1:
        problems.append(f"constraints.max_utilization must be in (0, 1], got {mu!r}")

    latency, source = resolve_latency(spec, topology, hosts, domain, problems)
    if problems:
        raise DeploymentSpecError(problems)
    return BoundSpec(
        hosts=hosts, host_cpu=host_cpu, host_mem=host_mem, host_p=host_p, domain=domain,
        domain_p=domain_p, latency=latency, latency_source=source, cpu=cpu, mem_mb=mem,
        replicas=replicas, allowed=allowed, anti_affinity=constraints["anti_affinity"],
        use_all_hosts=constraints["use_all_hosts"], max_utilization=float(mu),
    )


def resolve_latency(spec: DeploymentSpec, topology: Dict[str, Any], hosts: List[str],
                    domain: Dict[str, str], problems: List[str]) -> Tuple[Dict[str, Dict[str, float]], str]:
    """Host-to-host latency in ms, and which rule produced it.

    Precedence per pair: an explicit ``matrix_ms`` entry (filled symmetrically),
    else CONNECTS_TO hops x ``per_hop_ms`` when the topology has a network (inf
    when unreachable), else the intra/inter-domain constants. Same host is 0.
    """
    lat = {**DEFAULT_LATENCY, **{k: v for k, v in spec.latency.items() if k != "matrix_ms"}}
    for key, value in lat.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            problems.append(f"latency.{key} must be a number >= 0, got {value!r}")
            lat[key] = 0.0

    explicit: Dict[Tuple[str, str], float] = {}
    matrix = spec.latency.get("matrix_ms") or {}
    host_set = set(hosts)
    for a in sorted(matrix):
        row = matrix[a] if isinstance(matrix[a], dict) else {}
        for b in sorted(row):
            v = row[b]
            if a not in host_set or b not in host_set:
                problems.append(f"latency.matrix_ms.{a}.{b}: not a Node in the topology")
            elif not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0:
                problems.append(f"latency.matrix_ms.{a}.{b} must be a number >= 0, got {v!r}")
            else:
                explicit[(a, b)] = explicit[(b, a)] = float(v)

    links = topology.get("relationships", {}).get("connects_to", [])
    hops: Optional[Dict[str, Dict[str, int]]] = None
    if links:
        vertices = hosts + [d["id"] for d in topology.get("network_devices", [])]
        fabric = Fabric(vertices, [(e["from"], e["to"], e.get("capacity_mbps")) for e in links])
        hops = {h: fabric.hops(h) for h in hosts}

    used = set()
    out: Dict[str, Dict[str, float]] = {}
    for a in hosts:
        out[a] = {}
        for b in hosts:
            if a == b:
                out[a][b] = 0.0
            elif (a, b) in explicit:
                out[a][b] = explicit[(a, b)]
                used.add("matrix")
            elif hops is not None:
                n = hops[a].get(b)
                out[a][b] = math.inf if n is None else n * float(lat["per_hop_ms"])
                used.add("hops")
            else:
                same = domain.get(a) == domain.get(b)
                out[a][b] = float(lat["intra_domain_ms" if same else "inter_domain_ms"])
                used.add("constants")
    source = used.pop() if len(used) == 1 else ("mixed" if used else "none")
    return out, source


def _positive(value: Any, where: str, problems: List[str]) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not value > 0:
        problems.append(f"{where} must be a number > 0, got {value!r}")
        return 1.0
    return float(value)


def _probability(value: Any, where: str, problems: List[str]) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1:
        problems.append(f"{where} must be in [0, 1], got {value!r}")
        return 0.0
    return float(value)


def _host_list(value: Any, where: str, hosts: set, problems: List[str]) -> Optional[List[str]]:
    if value is None:
        return None
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        problems.append(f"{where} must be a list of Node ids")
        return []
    problems.extend(f"{where}: {h} is not a Node in the topology" for h in value if h not in hosts)
    return [h for h in value if h in hosts]
