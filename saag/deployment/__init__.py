"""
Pre-deployment planning: map Applications and Brokers to Nodes.

See docs/deployment.md. The stage plans a deployment and verifies it on the
replica-aware failure simulator; it never deploys anything.
"""

from .models import Candidate, DeploymentResult, Diagnosis, HeldOutScore
from .service import DeploymentService
from .spec import BoundSpec, DeploymentSpec, DeploymentSpecError, bind_spec, load_spec
from .verify import apply_placement

__all__ = [
    "BoundSpec",
    "Candidate",
    "DeploymentResult",
    "DeploymentService",
    "DeploymentSpec",
    "DeploymentSpecError",
    "Diagnosis",
    "HeldOutScore",
    "apply_placement",
    "bind_spec",
    "load_spec",
]
