"""
GNN Architecture

HGT-based heterogeneous graph neural network for node and edge criticality
prediction, plus the multi-task loss it is trained with.
"""

from __future__ import annotations
import logging
from typing import Dict, List, Optional, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch import Tensor

# Import canonical dimension constants to avoid drift between data prep and model
from ..data_preparation import (
    NODE_TYPE_TO_DIM, EDGE_FEATURE_DIM, _EDGE_BASE_DIM, _QOS_EXTRA_DIM, LABEL_COLS, node_type_dim,
)

logger = logging.getLogger(__name__)


# ── GNN Support Classes ─────────────────────────────────────────────────────────

_hgtconv_patched = False


def _require_pyg():
    global _hgtconv_patched
    try:
        import torch_geometric  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "PyTorch Geometric is required for GNN functionality.\n"
            "Install with:  pip install torch-geometric"
        ) from exc
    if not _hgtconv_patched:
        _patch_hgtconv_device_bug()
        _hgtconv_patched = True


def _patch_hgtconv_device_bug():
    """Work around an upstream HGTConv device bug, still present through at least
    torch-geometric 2.8.0.post1 (checked directly against that wheel's source).
    `HGTConv._construct_src_node_feat` builds its per-relation type index with
    `torch.arange(H, dtype=torch.long)` — no `device=` — so on any CUDA run that
    CPU-resident index gets passed into `HeteroLinear`, which raises "indices
    should be either on cpu or on the same device as the indexed tensor". Invisible
    on a CPU-only dev machine since everything already matches there; reproduces on
    every GPU run (e.g. Colab) for the `hgl`/`hgl_qos` variants, the only ones using
    HGTConv. This reimplements the method with the missing `device=` added — no
    other behavior change.
    """
    from torch_geometric.nn import HGTConv

    def _construct_src_node_feat(self, k_dict, v_dict, edge_index_dict):
        cumsum = 0
        num_edge_types = len(self.edge_types)
        H, D = self.heads, self.out_channels // self.heads

        ks, vs, type_list = [], [], []
        offset = {}
        for edge_type in edge_index_dict.keys():
            src = edge_type[0]
            N = k_dict[src].size(0)
            offset[edge_type] = cumsum
            cumsum += N

            edge_type_offset = self.edge_types_map[edge_type]
            type_vec = torch.arange(
                H, dtype=torch.long, device=k_dict[src].device
            ).view(-1, 1).repeat(1, N) * num_edge_types + edge_type_offset

            type_list.append(type_vec)
            ks.append(k_dict[src])
            vs.append(v_dict[src])

        ks = torch.cat(ks, dim=0).transpose(0, 1).reshape(-1, D)
        vs = torch.cat(vs, dim=0).transpose(0, 1).reshape(-1, D)
        type_vec = torch.cat(type_list, dim=1).flatten()

        k = self.k_rel(ks, type_vec).view(H, -1, D).transpose(0, 1)
        v = self.v_rel(vs, type_vec).view(H, -1, D).transpose(0, 1)
        return k, v, offset

    HGTConv._construct_src_node_feat = _construct_src_node_feat


# NetworkDevice is appended, not inserted: a graph without switches/routers
# yields exactly the tensors and HGT metadata it did before it existed.
NODE_TYPES: List[str] = ["Application", "Broker", "Topic", "Node", "Library", "NetworkDevice"]
#: composite, reliability, maintainability. Derived from LABEL_COLS rather
#: than restated, so a label-contract change can't leave this out of sync —
#: exactly the defect class that once zeroed a whole prediction column.
NUM_LABEL_DIMS = len(LABEL_COLS)


class ResidualMLP(nn.Module):
    """Two-layer MLP with residual connection and layer norm."""

    def __init__(self, in_dim: int, hidden_dim: int, out_dim: int, dropout: float = 0.2):
        super().__init__()
        self.fc1 = nn.Linear(in_dim, hidden_dim)
        self.fc2 = nn.Linear(hidden_dim, out_dim)
        # LayerNorm on a 1-element vector collapses to 0 (mean=x, var=0 → output=beta=0).
        # Use Identity when out_dim=1 so the scalar residual is preserved.
        self.norm = nn.LayerNorm(out_dim) if out_dim > 1 else nn.Identity()
        self.dropout = nn.Dropout(dropout)
        self.proj = nn.Linear(in_dim, out_dim) if in_dim != out_dim else nn.Identity()

    def forward(self, x: Tensor) -> Tensor:
        h = F.gelu(self.fc1(x))
        h = self.dropout(h)
        h = self.fc2(h)
        return self.norm(h + self.proj(x))


class EdgeFeatureEncoder(nn.Module):
    """Aggregates edge features into destination-node embeddings via scatter-mean.

    Called before each HGTConv layer to inject edge information, since HGTConv
    does not accept raw edge_attr tensors.
    """

    def __init__(self, edge_feat_dim: int, hidden_channels: int):
        super().__init__()
        self.proj = nn.Linear(edge_feat_dim, hidden_channels)

    def forward(
        self,
        h_dict: Dict[str, Tensor],
        edge_index_dict: Dict,
        edge_attr_dict: Dict,
    ) -> Dict[str, Tensor]:
        try:
            from torch_scatter import scatter_mean
        except ImportError:
            # Fallback: manual scatter via index_add
            def scatter_mean(src, index, dim, dim_size):
                index = index.to(src.device)
                out = torch.zeros(dim_size, src.size(1), device=src.device, dtype=src.dtype)
                count = torch.zeros(dim_size, 1, device=src.device, dtype=src.dtype)
                out.index_add_(0, index, src)
                count.index_add_(0, index, torch.ones(src.size(0), 1, device=src.device))
                return out / count.clamp(min=1)

        augmented = {k: v.clone() for k, v in h_dict.items()}
        for rel, edge_index in edge_index_dict.items():
            _, _, dst_type = rel
            if rel not in edge_attr_dict or dst_type not in h_dict:
                continue
            e = self.proj(edge_attr_dict[rel])          # (E, hidden)
            n_dst = h_dict[dst_type].size(0)
            aggr = scatter_mean(e, edge_index[1], dim=0, dim_size=n_dst)
            augmented[dst_type] = augmented[dst_type] + aggr
        return augmented


class TypedQoSEdgeFeatureEncoder(nn.Module):
    """Per-relation-type alternative to `EdgeFeatureEncoder`'s pooled injection.

    `EdgeFeatureEncoder` projects the full ``EDGE_FEATURE_DIM`` (16) vector —
    weight, path_count_norm, a 7-dim edge-type one-hot, and 7 QoS dims
    (reliability_score, durability_score, priority_score, has_deadline,
    deadline_ns_log, max_blocking_ms_log, qos_heterogeneity_flag) — through
    ONE shared linear layer, so QoS signal is averaged together with the
    edge-type one-hot before HGTConv ever sees it. A null RQ3 result
    measured through that path measures QoS-after-mean-pooling with a
    structurally dominant one-hot, not QoS encoding on its own merits.

    This variant splits the two apart: the base 9 dims keep the existing
    shared projection (this is not what RQ3 is asking about), while the 7
    QoS dims get their own linear projection **per relation type** — a
    PUBLISHES_TO edge's QoS contract and a RUNS_ON edge's are not the same
    kind of signal, and one shared matrix cannot represent that. The two
    projected components are aggregated into the destination node
    separately and summed, so the QoS pathway's weights are never
    entangled with the base pathway's.
    """

    def __init__(
        self,
        hidden_channels: int,
        edge_types: List[Tuple],
        base_dim: int = _EDGE_BASE_DIM,
        qos_dim: int = _QOS_EXTRA_DIM,
    ):
        super().__init__()
        self.base_dim = base_dim
        self.qos_dim = qos_dim
        self.base_proj = nn.Linear(base_dim, hidden_channels)
        self.qos_proj = nn.ModuleDict({
            TypedEdgeEncoder._rel_key(rel): nn.Linear(qos_dim, hidden_channels)
            for rel in edge_types
        })

    def forward(
        self,
        h_dict: Dict[str, Tensor],
        edge_index_dict: Dict,
        edge_attr_dict: Dict,
    ) -> Dict[str, Tensor]:
        try:
            from torch_scatter import scatter_mean
        except ImportError:
            def scatter_mean(src, index, dim, dim_size):
                index = index.to(src.device)
                out = torch.zeros(dim_size, src.size(1), device=src.device, dtype=src.dtype)
                count = torch.zeros(dim_size, 1, device=src.device, dtype=src.dtype)
                out.index_add_(0, index, src)
                count.index_add_(0, index, torch.ones(src.size(0), 1, device=src.device))
                return out / count.clamp(min=1)

        augmented = {k: v.clone() for k, v in h_dict.items()}
        for rel, edge_index in edge_index_dict.items():
            _, _, dst_type = rel
            if rel not in edge_attr_dict or dst_type not in h_dict:
                continue
            attr = edge_attr_dict[rel]
            n_dst = h_dict[dst_type].size(0)

            base = self.base_proj(attr[:, :self.base_dim])
            aggr = scatter_mean(base, edge_index[1], dim=0, dim_size=n_dst)
            augmented[dst_type] = augmented[dst_type] + aggr

            rel_key = TypedEdgeEncoder._rel_key(rel)
            if rel_key in self.qos_proj and attr.shape[1] >= self.base_dim + self.qos_dim:
                qos = self.qos_proj[rel_key](attr[:, self.base_dim:self.base_dim + self.qos_dim])
                qos_aggr = scatter_mean(qos, edge_index[1], dim=0, dim_size=n_dst)
                augmented[dst_type] = augmented[dst_type] + qos_aggr
        return augmented


class TypedEdgeEncoder(nn.Module):
    """Per-relation-type edge encoder for EdgeCriticalityGNN.

    Uses relation-specific linear projections instead of a shared MLP,
    which better captures the semantics of different edge types.
    """

    def __init__(
        self,
        edge_feat_dim: int,
        hidden_channels: int,
        edge_types: List[Tuple],
        dropout: float = 0.2,
    ):
        super().__init__()
        self.type_proj = nn.ModuleDict({
            self._rel_key(rel): nn.Linear(edge_feat_dim, hidden_channels)
            for rel in edge_types
        })
        self.fuse = nn.Sequential(
            nn.Linear(hidden_channels * 3, hidden_channels),
            nn.LayerNorm(hidden_channels),
            nn.GELU(),
        )
        self.out_head = ResidualMLP(hidden_channels, hidden_channels // 2, NUM_LABEL_DIMS, dropout)

    @staticmethod
    def _rel_key(rel: Tuple) -> str:
        return "__".join(str(r) for r in rel)

    def forward(self, h_src: Tensor, h_dst: Tensor, e_feat: Tensor, rel: Tuple) -> Tensor:
        key = self._rel_key(rel)
        if key in self.type_proj:
            e_proj = self.type_proj[key](e_feat)
        else:
            e_proj = torch.zeros(e_feat.size(0), h_src.size(-1), device=h_src.device)
        fused = self.fuse(torch.cat([h_src, h_dst, e_proj], dim=-1))
        return torch.sigmoid(self.out_head(fused))


#: Node types that carry a closed-form Topo-QoS score, and so a prior.
_PRIOR_NODE_TYPES = ("Application", "Library")


class NodeCriticalityGNN(nn.Module):
    """Heterogeneous Graph Transformer (HGT) for node-level criticality prediction.

    Architecture:
    - Per-type input projections → hidden_channels
    - N layers of HGTConv with EdgeFeatureEncoder injecting edge info before each layer
    - Optional bidirectional pass (forward + reverse) for upstream/downstream awareness
    - Two RM output heads (reliability, maintainability) + one composite head
      (all sigmoid-activated)
    """

    def __init__(
        self,
        metadata: Tuple,
        hidden_channels: int = 64,
        num_heads: int = 4,
        num_layers: int = 3,
        dropout: float = 0.2,
        use_bidirectional: bool = True,
        qos_injection: str = "pooled",
        topo_prior: bool = False,
    ):
        _require_pyg()
        super().__init__()
        from torch_geometric.nn import HGTConv

        node_types, edge_types = metadata
        self.node_types = node_types
        self.edge_types = edge_types
        self.hidden_channels = hidden_channels
        self.num_layers = num_layers
        self.dropout_p = dropout
        self.out_dims = NUM_LABEL_DIMS
        self.use_bidirectional = use_bidirectional
        if qos_injection not in ("pooled", "typed"):
            raise ValueError(f"qos_injection must be 'pooled' or 'typed', got {qos_injection!r}")
        self.qos_injection = qos_injection
        # SaG-Hybrid (PREREGISTRATION.md, Amendment 5): the last input column
        # of every node type carries the rank-normalised closed-form Topo-QoS
        # score, and the composite head learns a correction on its logit.
        self.topo_prior = topo_prior
        extra = 1 if topo_prior else 0

        # Per-type input projections — dims sourced from data_preparation constants
        self.input_proj = nn.ModuleDict({
            nt: nn.Sequential(
                nn.Linear(node_type_dim(nt) + extra, hidden_channels),
                nn.LayerNorm(hidden_channels),
                nn.GELU(),
            )
            for nt in node_types
        })

        # HGTConv layers with edge feature injection and per-layer residual norms
        self.convs = nn.ModuleList([
            HGTConv(
                in_channels=hidden_channels,
                out_channels=hidden_channels,
                metadata=metadata,
                heads=num_heads,
            )
            for _ in range(num_layers)
        ])
        # "pooled" (default): EDGE_FEATURE_DIM projected through one shared
        # linear layer, QoS dims mean-pooled together with the edge-type
        # one-hot. "typed": TypedQoSEdgeFeatureEncoder — QoS gets its own
        # per-relation-type projection, summed in separately (RQ3 re-test).
        self.edge_encoders = nn.ModuleList([
            (
                TypedQoSEdgeFeatureEncoder(hidden_channels, edge_types)
                if qos_injection == "typed"
                else EdgeFeatureEncoder(EDGE_FEATURE_DIM, hidden_channels)
            )
            for _ in range(num_layers)
        ])
        self.norms = nn.ModuleList([
            nn.ModuleDict({nt: nn.LayerNorm(hidden_channels) for nt in node_types})
            for _ in range(num_layers)
        ])
        self.dropouts = nn.ModuleList([nn.Dropout(dropout) for _ in range(num_layers)])

        # Optional reverse-direction HGTConv for bidirectional awareness
        if use_bidirectional:
            rev_edge_types = [
                (dst, "rev__" + etype, src)
                for (src, etype, dst) in edge_types
            ]
            rev_metadata = (node_types, rev_edge_types)
            self.rev_conv = HGTConv(
                in_channels=hidden_channels,
                out_channels=hidden_channels,
                metadata=rev_metadata,
                heads=num_heads,
            )
        else:
            self.rev_conv = None

        # Output heads. Fault tolerance and availability are Reliability sub-
        # characteristics scored on the analysis side (see saag/analysis/), not
        # separate GNN prediction targets — the GNN predicts the two RM label
        # columns (reliability, maintainability) plus the composite.
        self.rm_heads = nn.ModuleDict({
            dim: ResidualMLP(hidden_channels, hidden_channels // 2, 1, dropout)
            for dim in ["reliability", "maintainability"]
        })
        self.composite_head = ResidualMLP(hidden_channels + 2, hidden_channels // 2, 1, dropout)
        if topo_prior:
            self.prior_alpha = nn.Parameter(torch.tensor(1.0))

    def _apply_reverse_pass(
        self, h: Dict[str, Tensor], edge_index_dict: Dict
    ) -> Dict[str, Tensor]:
        """Single reverse-direction HGTConv pass for upstream signal propagation."""
        rev_ei = {
            (dst, "rev__" + etype, src): torch.stack([ei[1], ei[0]])
            for (src, etype, dst), ei in edge_index_dict.items()
        }
        if not rev_ei:
            return h
        h_rev = self.rev_conv(h, rev_ei)
        for nt, h_r in h_rev.items():
            if nt in h:
                h[nt] = h[nt] + 0.5 * h_r
        return h

    def encode(
        self,
        x_dict: Dict[str, Tensor],
        edge_index_dict: Dict,
        edge_attr_dict: Optional[Dict] = None,
    ) -> Dict[str, Tensor]:
        h: Dict[str, Tensor] = {
            nt: self.input_proj[nt](x)
            for nt, x in x_dict.items()
            if nt in self.input_proj
        }

        for conv, edge_enc, norm_d, drop in zip(
            self.convs, self.edge_encoders, self.norms, self.dropouts
        ):
            if edge_attr_dict:
                h = edge_enc(h, edge_index_dict, edge_attr_dict)
            h_new = conv(h, edge_index_dict)
            for nt, h_n in h_new.items():
                residual = h.get(nt, torch.zeros_like(h_n))
                h[nt] = drop(F.gelu(norm_d[nt](h_n + residual)))

        if self.use_bidirectional and self.rev_conv is not None:
            h = self._apply_reverse_pass(h, edge_index_dict)

        return h

    def decode(
        self, h_dict: Dict[str, Tensor], prior_dict: Optional[Dict[str, Tensor]] = None
    ) -> Dict[str, Tensor]:
        out: Dict[str, Tensor] = {}
        for nt, h in h_dict.items():
            r = torch.sigmoid(self.rm_heads["reliability"](h))
            m = torch.sigmoid(self.rm_heads["maintainability"](h))
            composite_in = torch.cat([h, r, m], dim=-1)
            z = self.composite_head(composite_in)
            if self.topo_prior and prior_dict is not None and nt in _PRIOR_NODE_TYPES:
                p = prior_dict[nt].clamp(0.01, 0.99).unsqueeze(-1)
                z = z + self.prior_alpha * torch.logit(p)
            composite = torch.sigmoid(z)
            out[nt] = torch.cat([composite, r, m], dim=-1)
        return out

    def forward(self, x_dict, edge_index_dict, edge_attr_dict=None):
        h_dict = self.encode(x_dict, edge_index_dict, edge_attr_dict)
        prior_dict = (
            {nt: x[:, -1] for nt, x in x_dict.items()} if self.topo_prior else None
        )
        return self.decode(h_dict, prior_dict)

    def get_embeddings(self, x_dict, edge_index_dict, edge_attr_dict=None):
        return self.encode(x_dict, edge_index_dict, edge_attr_dict)


class EdgeCriticalityGNN(nn.Module):
    """Predicts criticality of pub-sub relationships using TypedEdgeEncoder."""

    def __init__(
        self,
        node_gnn: NodeCriticalityGNN,
        hidden_channels: int = 64,
        dropout: float = 0.2,
    ):
        super().__init__()
        self.node_gnn = node_gnn
        self.predict_edges = True
        self.typed_edge_enc = TypedEdgeEncoder(
            edge_feat_dim=EDGE_FEATURE_DIM,
            hidden_channels=hidden_channels,
            edge_types=node_gnn.edge_types,
            dropout=dropout,
        )

    def forward(self, x_dict, edge_index_dict, edge_attr_dict=None):
        h_dict = self.node_gnn.get_embeddings(x_dict, edge_index_dict, edge_attr_dict)
        prior_dict = (
            {nt: x[:, -1] for nt, x in x_dict.items()} if self.node_gnn.topo_prior else None
        )
        node_preds = self.node_gnn.decode(h_dict, prior_dict)
        edge_preds = {}
        for rel, edge_index in edge_index_dict.items():
            src_type, _, dst_type = rel
            if src_type not in h_dict or dst_type not in h_dict:
                continue
            src_idx, dst_idx = edge_index[0], edge_index[1]
            h_src = h_dict[src_type][src_idx]
            h_dst = h_dict[dst_type][dst_idx]
            if edge_attr_dict and rel in edge_attr_dict:
                e_feat = edge_attr_dict[rel]
            else:
                e_feat = torch.zeros(
                    edge_index.size(1), EDGE_FEATURE_DIM, device=h_src.device
                )
            edge_preds[rel] = self.typed_edge_enc(h_src, h_dst, e_feat, rel)
        return node_preds, edge_preds


# ── Loss functions ─────────────────────────────────────────────────────────────

class CriticalityLoss(nn.Module):
    """Multi-task criticality loss with consistency regularization and pairwise ranking.

    Loss components:
    - MSE on composite score (labeled nodes)
    - MSE on RM sub-scores (labeled nodes, multitask)
    - ListMLE ranking loss on composite (labeled nodes)
    - Pairwise margin ranking loss on composite (labeled nodes)
    - RM consistency regularization on unlabeled nodes (rm_consistency_weight,
      default 0.0 — off). This term regresses the predicted R/M heads toward
      the RM diagnostic pathway's own R(v)/M(v) on nodes the oracle never
      labeled, which is a real coupling between the two pathways when active:
      set it > 0 explicitly to opt into that ablation arm, e.g. 0.1 reproduces
      the pre-decoupling default.
    """

    def __init__(
        self,
        multitask_weight: float = 0.5,
        rm_consistency_weight: float = 0.0,
        ranking_weight: float = 0.3,
        pairwise_ranking_weight: float = 0.1,
        temperature: float = 1.0,
        pairwise_margin: float = 0.05,
        ranking_loss: str = "listmle",
    ):
        super().__init__()
        if ranking_loss not in ("listmle", "listmle_ties"):
            raise ValueError(f"unknown ranking_loss {ranking_loss!r}")
        #: ``"listmle"`` breaks label ties in input order; ``"listmle_ties"``
        #: (Amendment 19) gives every member of a tie group the same risk set.
        self.ranking_loss = ranking_loss
        self.multitask_weight = multitask_weight
        self.rm_consistency_weight = rm_consistency_weight
        self.ranking_weight = ranking_weight
        self.pairwise_ranking_weight = pairwise_ranking_weight
        self.temperature = max(float(temperature), 1e-4)
        self.pairwise_margin = pairwise_margin
        self.mse = nn.MSELoss(reduction="mean")

    def forward(
        self,
        pred: Tensor,
        target: Tensor,
        mask: Tensor,
        rm_target: Optional[Tensor] = None,
        dim_weights: Optional[Tensor] = None,
    ) -> Tuple[Tensor, Dict[str, float]]:
        labeled_pred = pred[mask]
        labeled_target = target[mask]

        if labeled_pred.shape[0] == 0:
            zero = torch.tensor(0.0, device=pred.device)
            return zero, {"composite": 0.0, "multitask": 0.0, "ranking": 0.0,
                          "pairwise": 0.0, "consistency": 0.0}

        loss_composite = self.mse(labeled_pred[:, 0], labeled_target[:, 0])
        loss_multitask = self._multitask_loss(labeled_pred, labeled_target, dim_weights)
        listwise = self._listmle_ties_loss if self.ranking_loss == "listmle_ties" else self._listmle_loss
        loss_ranking = listwise(labeled_pred[:, 0], labeled_target[:, 0], temperature=self.temperature)
        loss_pairwise = self._pairwise_margin_loss(labeled_pred[:, 0], labeled_target[:, 0], margin=self.pairwise_margin)

        supervised_loss = (
            loss_composite
            + self.multitask_weight * loss_multitask
            + self.ranking_weight * loss_ranking
            + self.pairwise_ranking_weight * loss_pairwise
        )

        loss_consistency = torch.tensor(0.0, device=pred.device)
        if rm_target is not None:
            unlabeled_mask = ~mask
            unlabeled_pred = pred[unlabeled_mask]
            unlabeled_rm = rm_target[unlabeled_mask]
            if unlabeled_pred.shape[0] > 0:
                loss_consistency = self.mse(unlabeled_pred[:, 1:], unlabeled_rm[:, 1:])

        total = supervised_loss + self.rm_consistency_weight * loss_consistency

        components = {
            "composite": loss_composite.item(),
            "multitask": loss_multitask.item(),
            "ranking": loss_ranking.item(),
            "pairwise": loss_pairwise.item(),
            "consistency": loss_consistency.item(),
        }
        return total, components

    def _multitask_loss(
        self,
        labeled_pred: Tensor,
        labeled_target: Tensor,
        dim_weights: Optional[Tensor],
    ) -> Tensor:
        """MSE over the RM sub-scores, skipping dimensions the labeler never measured.

        `dim_weights` is a vector aligned with LABEL_COLS (length NUM_LABEL_DIMS);
        a 0 entry excludes that dimension. Without it, a labeler that reports only
        a scalar impact (FaultInjector measures composite/reliability) would have
        its maintainability head regressed toward a constant zero — training the
        model to predict an artefact of the label format.
        """
        if dim_weights is None:
            return self.mse(labeled_pred[:, 1:], labeled_target[:, 1:])

        w = dim_weights[1:].to(labeled_pred.device, labeled_pred.dtype)
        total_w = w.sum()
        if total_w <= 0:
            return torch.tensor(0.0, device=labeled_pred.device)

        sq_err = (labeled_pred[:, 1:] - labeled_target[:, 1:]) ** 2
        return (sq_err * w).sum() / (total_w * labeled_pred.shape[0])

    @staticmethod
    def _listmle_loss(scores: Tensor, targets: Tensor, temperature: float = 1.0) -> Tensor:
        """ListMLE listwise ranking loss under Plackett-Luce model with temperature scaling.

        When scores are sigmoid-bounded in [0, 1], temperature tau < 1.0 sharpens
        the Plackett-Luce distribution, preventing gradient saturation from bounded
        odds ratios (exp(1)/exp(0) ≈ 2.718).
        """
        _, idx = torch.sort(targets, descending=True)
        tau = max(float(temperature), 1e-4)
        sorted_scores = scores[idx] / tau
        cum_lse = torch.logcumsumexp(sorted_scores.flip(0), dim=0).flip(0)
        log_probs = sorted_scores - cum_lse
        return -log_probs.mean()

    @staticmethod
    def _listmle_ties_loss(scores: Tensor, targets: Tensor, temperature: float = 1.0) -> Tensor:
        """ListMLE with tied labels as groups (Breslow), so input order cannot matter.

        Plackett-Luce over the label order, except that each item's risk set starts
        at the first position of its tie group: every member of a group is scored
        against the whole group and everything below it. Identical to
        :meth:`_listmle_loss` when no labels tie (Amendment 19).
        """
        sorted_targets, idx = torch.sort(targets, descending=True, stable=True)
        tau = max(float(temperature), 1e-4)
        sorted_scores = scores[idx] / tau
        cum_lse = torch.logcumsumexp(sorted_scores.flip(0), dim=0).flip(0)
        n = sorted_targets.shape[0]
        pos = torch.arange(n, device=scores.device)
        new_group = torch.ones(n, dtype=torch.bool, device=scores.device)
        new_group[1:] = sorted_targets[1:] != sorted_targets[:-1]
        start = torch.cummax(torch.where(new_group, pos, torch.zeros_like(pos)), dim=0).values
        log_probs = sorted_scores - cum_lse[start]
        return -log_probs.mean()

    @staticmethod
    def _pairwise_margin_loss(
        scores: Tensor, targets: Tensor, margin: float = 0.05
    ) -> Tensor:
        """For all pairs (i,j) where target_i - target_j > margin, penalize score_i < score_j + margin."""
        n = scores.shape[0]
        if n < 2:
            return torch.tensor(0.0, device=scores.device)
        s_diff = scores.unsqueeze(0) - scores.unsqueeze(1)    # (n, n)
        t_diff = targets.unsqueeze(0) - targets.unsqueeze(1)  # (n, n)
        should_rank = (t_diff > margin).float()
        loss = torch.clamp(margin - s_diff, min=0.0) * should_rank
        return loss.sum() / (should_rank.sum() + 1e-8)


def build_node_gnn(
    metadata,
    hidden_channels: int = 64,
    num_heads: int = 4,
    num_layers: int = 3,
    dropout: float = 0.2,
    use_bidirectional: bool = True,
    qos_injection: str = "pooled",
    topo_prior: bool = False,
) -> NodeCriticalityGNN:
    return NodeCriticalityGNN(
        metadata, hidden_channels, num_heads, num_layers, dropout,
        use_bidirectional=use_bidirectional, qos_injection=qos_injection,
        topo_prior=topo_prior,
    )


def build_edge_gnn(
    metadata,
    hidden_channels: int = 64,
    num_heads: int = 4,
    num_layers: int = 3,
    dropout: float = 0.2,
    use_bidirectional: bool = True,
    qos_injection: str = "pooled",
    topo_prior: bool = False,
) -> EdgeCriticalityGNN:
    node_gnn = build_node_gnn(
        metadata, hidden_channels, num_heads, num_layers, dropout, use_bidirectional,
        qos_injection=qos_injection, topo_prior=topo_prior,
    )
    return EdgeCriticalityGNN(node_gnn, hidden_channels=hidden_channels, dropout=dropout)
