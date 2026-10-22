"""
saag/usecases/deploy_graph.py
"""
from pathlib import Path
from typing import Any, Dict, Union

from saag.deployment.models import DeploymentResult
from saag.deployment.service import DeploymentService
from saag.deployment.spec import DeploymentSpec


class DeployGraphUseCase:
    """
    Use case for planning an application-to-node deployment and verifying it on
    the held-out failure simulator. It plans; it never deploys.
    """

    def __init__(self, service: DeploymentService):
        self.service = service

    def execute(self, spec: Union[DeploymentSpec, Dict[str, Any], str, Path], **kwargs: Any) -> DeploymentResult:
        """Forward to DeploymentService.optimize; ``kwargs`` carries the solver and verification options."""
        return self.service.optimize(spec, **kwargs)
