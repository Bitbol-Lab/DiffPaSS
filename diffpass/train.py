# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/train.ipynb.

# %% auto 0
__all__ = ['IndexPair', 'IndexPairsInGroup', 'IndexPairsInGroups', 'InformationPairing', 'BestHitsPairing', 'MirrortreePairing',
           'GraphAlignment']

# %% ../nbs/train.ipynb 4
# Stdlib imports
from collections.abc import Sequence
from typing import Optional, Any, Literal

# NumPy
import numpy as np

# PyTorch
import torch

# DiffPaSS imports
from .base import DiffPaSSModel
from diffpass.model import (
    MatrixApply,
    PermutationConjugate,
    apply_hard_permutation_batch_to_similarity,
    TwoBodyEntropyLoss,
    MILoss,
    InterGroupSimilarityLoss,
    IntraGroupSimilarityLoss,
)

# Type aliases
IndexPair = tuple[int, int]  # Pair of indices
IndexPairsInGroup = list[IndexPair]  # Pairs of indices in a group of sequences
IndexPairsInGroups = list[IndexPairsInGroup]  # Pairs of indices in groups of sequences

# %% ../nbs/train.ipynb 6
class InformationPairing(DiffPaSSModel):
    """DiffPaSS model for information-theoretic pairing of multiple sequence alignments (MSAs)."""

    are_inputs_msas = True

    def __init__(
        self,
        # Number of sequences in each group (e.g. species) of the two MSAs
        group_sizes: Sequence[int],
        # If not ``None``, fixed pairings between groups, of the form [[(i1, j1), (i2, j2), ...], ...] where (i1, j1) are the indices of the first fixed pair in the first group to be paired, etc.
        fixed_pairings: Optional[IndexPairsInGroups] = None,
        # If not ``None``, configuration dictionary containing init parameters for the internal `GeneralizedPermutation` object to compute soft/hard permutations
        permutation_cfg: Optional[dict[str, Any]] = None,
        # Information-theoretic measure to use. For hard permutations, these two measures are equivalent
        information_measure: Literal["MI", "TwoBodyEntropy"] = "TwoBodyEntropy",
    ):
        super().__init__()

        # Initialize permutation and matrix apply modules
        # (self.permutation and self.matrix_apply)
        self.init_permutation(
            group_sizes=group_sizes,
            fixed_pairings=fixed_pairings,
            permutation_cfg=permutation_cfg,
        )
        self.matrix_apply = MatrixApply(group_sizes=self.group_sizes)

        # Initialize information-theoretic loss module
        self.validate_information_measure(information_measure)
        self.information_measure = information_measure
        if self.information_measure == "TwoBodyEntropy":
            self.information_loss = TwoBodyEntropyLoss()
        elif self.information_measure == "MI":
            self.information_loss = MILoss()

    def forward(
        self,
        x: torch.Tensor,
        y: torch.Tensor,
    ) -> dict[str, torch.Tensor]:
        # Soft or hard permutations (list)
        perms = self.permutation()
        x_perm = self.matrix_apply(x, mats=perms)

        # Two-body entropy portion of the loss
        loss = self.information_loss(x_perm, y)

        return {"perms": perms, "x_perm": x_perm, "loss": loss}

    def prepare_fit(self, x: torch.Tensor, y: torch.Tensor) -> None:
        # Validate inputs
        self.validate_inputs(x, y, check_same_alphabet_size=True)

    def compute_losses_identity_perm(
        self, x: torch.Tensor, y: torch.Tensor
    ) -> dict[str, float]:
        # Compute hard/soft losses when using identity permutation
        self.hard_()
        with torch.no_grad():
            hard_loss_identity_perm = self.information_loss(x, y).item()
            soft_loss_identity_perm = hard_loss_identity_perm

        return {"hard": hard_loss_identity_perm, "soft": soft_loss_identity_perm}

# %% ../nbs/train.ipynb 9
class BestHitsPairing(DiffPaSSModel):
    """DiffPaSS model for pairing of multiple sequence alignments (MSAs) by aligning their orthology networks, constructed using (reciprocal) best hits ."""

    are_inputs_msas = True

    def __init__(
        self,
        # Number of sequences in each group (e.g. species) of the two MSAs
        group_sizes: Sequence[int],
        # If not ``None``, fixed pairings between groups, of the form [[(i1, j1), (i2, j2), ...], ...] where (i1, j1) are the indices of the first fixed pair in the first group to be paired, etc.
        fixed_pairings: Optional[IndexPairsInGroups] = None,
        # If not ``None``, configuration dictionary containing init parameters for the internal `GeneralizedPermutation` object to compute soft/hard permutations
        permutation_cfg: Optional[dict[str, Any]] = None,
        # (Smoothly extended) similarity metric to use on all pairs of aligned sequences
        similarity_kind: Literal["Hamming", "Blosum62"] = "Hamming",
        # If not ``None``, configuration dictionary containing init parameters for the internal `HammingSimilarities` or `Blosum62Similarities` object to compute similarity matrices
        similarities_cfg: Optional[dict[str, Any]] = None,
        # Whether to also compute best hits within each group (in addition to between different groups)
        compute_in_group_best_hits: bool = True,
        # If not ``None``, configuration dictionary containing init parameters for the internal `BestHits` object to compute soft/hard (reciprocal) best hits
        best_hits_cfg: Optional[dict[str, Any]] = None,
        # If not ``None``, custom callable to compute the differentiable loss between the soft/hard best hits matrices of the two MSAs
        similarities_comparison_loss: Optional[callable] = None,
        # Whether to compare the soft best hits from the MSA to permute (``x``) to the hard or soft best hits from the reference MSA (``y``)
        compare_soft_best_hits_to_hard: bool = True,
    ):
        super().__init__()

        # Initialize permutation and matrix apply modules
        # (self.permutation and self.matrix_apply)
        self.init_permutation(
            group_sizes=group_sizes,
            fixed_pairings=fixed_pairings,
            permutation_cfg=permutation_cfg,
        )
        self.matrix_apply = MatrixApply(group_sizes=self.group_sizes)

        # Validate similarity kind/config and initialize similarities module
        self.init_similarities(
            similarity_kind=similarity_kind, similarities_cfg=similarities_cfg
        )

        # Validate best hits config and initialize best hits module
        self.compute_in_group_best_hits = compute_in_group_best_hits
        self.init_best_hits(best_hits_cfg)

        self.compare_soft_best_hits_to_hard = compare_soft_best_hits_to_hard

        # Similarities comparison loss
        self.similarities_comparison_loss = similarities_comparison_loss
        if self.similarities_comparison_loss is None:
            self.effective_similarities_comparison_loss_ = InterGroupSimilarityLoss(
                group_sizes=self.group_sizes
            )
        else:
            self.effective_similarities_comparison_loss_ = (
                self.similarities_comparison_loss
            )

    def _precompute_bh(self, x: torch.Tensor, y: torch.Tensor) -> None:
        mode = self.best_hits.mode

        # Temporarily switch to hard BH
        self.best_hits.hard_()
        similarities_x = self.similarities(x)
        self.register_buffer("_bh_hard_x", self.best_hits(similarities_x))
        similarities_y = self.similarities(y)
        self.register_buffer("_bh_hard_y", self.best_hits(similarities_y))

        # Switch to soft BH
        self.best_hits.soft_()
        self.register_buffer("_bh_soft_x", self.best_hits(similarities_x))
        self.register_buffer("_bh_soft_y", self.best_hits(similarities_y))

        # Restore initial mode
        self.best_hits.mode = mode

    @property
    def _bh_y_for_soft_x(self):
        if self.compare_soft_best_hits_to_hard:
            return self._bh_hard_y
        return self._bh_soft_y

    def forward(
        self, x: torch.Tensor, y: Optional[torch.Tensor] = None
    ) -> dict[str, torch.Tensor]:
        mode = self.permutation.mode
        assert (
            mode == self.best_hits.mode
        ), "Permutation and best hits must be either both in soft mode or both in hard mode."

        # Soft or hard permutations
        perms = self.permutation()
        x_perm = self.matrix_apply(x, mats=perms)

        # Best hits loss, with shortcut for hard permutations
        if mode == "soft":
            similarities_x = self.similarities(x_perm)
            bh_x = self.best_hits(similarities_x)
            # Ensure comparisons are soft_x-{soft,hard}_y, depending on
            # self.compare_soft_best_hits_to_hard
            loss = self.effective_similarities_comparison_loss_(
                bh_x, self._bh_y_for_soft_x
            )
        else:
            bh_x = apply_hard_permutation_batch_to_similarity(
                x=self._bh_hard_x, perms=perms
            )
            loss = self.effective_similarities_comparison_loss_(bh_x, self._bh_hard_y)

        return {
            "perms": perms,
            "x_perm": x_perm,
            "loss": loss,
        }

    def prepare_fit(self, x: torch.Tensor, y: torch.Tensor) -> None:
        # Validate inputs
        self.validate_inputs(x, y, check_same_alphabet_size=True)

        # Precompute matrices of best hits
        self._precompute_bh(x, y)

    def compute_losses_identity_perm(
        self, x: torch.Tensor, y: torch.Tensor
    ) -> dict[str, float]:
        # Precompute matrices of best hits
        self._precompute_bh(x, y)

        # Compute hard/soft losses when using identity permutation
        with torch.no_grad():
            hard_loss_identity_perm = self.effective_similarities_comparison_loss_(
                self._bh_hard_x, self._bh_hard_y
            ).item()
            soft_loss_identity_perm = self.effective_similarities_comparison_loss_(
                self._bh_soft_x, self._bh_y_for_soft_x
            ).item()

        return {"hard": hard_loss_identity_perm, "soft": soft_loss_identity_perm}

# %% ../nbs/train.ipynb 12
class MirrortreePairing(DiffPaSSModel):
    """DiffPaSS model for pairing of multiple sequence alignments (MSAs) by aligning their sequence distance networks as in the Mirrortree method."""

    are_inputs_msas = True

    def __init__(
        self,
        # Number of sequences in each group (e.g. species) of the two MSAs
        group_sizes: Sequence[int],
        # If not ``None``, fixed pairings between groups, of the form [[(i1, j1), (i2, j2), ...], ...] where (i1, j1) are the indices of the first fixed pair in the first group to be paired, etc.
        fixed_pairings: Optional[IndexPairsInGroups] = None,
        # If not ``None``, configuration dictionary containing init parameters for the internal `GeneralizedPermutation` object to compute soft/hard permutations
        permutation_cfg: Optional[dict[str, Any]] = None,
        # (Smoothly extended) similarity metric to use on all pairs of aligned sequences
        similarity_kind: Literal["Hamming", "Blosum62"] = "Hamming",
        # If not ``None``, configuration dictionary containing init parameters for the internal `HammingSimilarities` or `Blosum62Similarities` object to compute similarity matrices
        similarities_cfg: Optional[dict[str, Any]] = None,
        # If not ``None``, custom callable to compute the differentiable loss between the similarity matrix of the two MSAs. Default: `IntraGroupSimilarityLoss`
        similarities_comparison_loss: Optional[callable] = None,
    ):
        super().__init__()

        # Initialize permutation and matrix apply modules
        # (self.permutation and self.matrix_apply)
        self.init_permutation(
            group_sizes=group_sizes,
            fixed_pairings=fixed_pairings,
            permutation_cfg=permutation_cfg,
        )
        self.matrix_apply = MatrixApply(group_sizes=self.group_sizes)

        # Validate similarity kind/config and initialize similarities module
        self.init_similarities(
            similarity_kind=similarity_kind, similarities_cfg=similarities_cfg
        )

        #  Similarities comparison loss
        self.similarities_comparison_loss = similarities_comparison_loss
        if self.similarities_comparison_loss is None:
            self.effective_similarities_comparison_loss_ = IntraGroupSimilarityLoss(
                group_sizes=self.group_sizes
            )
        else:
            self.effective_similarities_comparison_loss_ = (
                self.similarities_comparison_loss
            )

    def _precompute_similarities(self, x: torch.Tensor, y: torch.Tensor) -> None:
        self.register_buffer("_similarities_hard_x", self.similarities(x))
        self.register_buffer("_similarities_hard_y", self.similarities(y))

    def forward(
        self, x: torch.Tensor, y: Optional[torch.Tensor] = None
    ) -> dict[str, torch.Tensor]:
        mode = self.permutation.mode

        # Soft or hard permutations (list)
        perms = self.permutation()
        x_perm = self.matrix_apply(x, mats=perms)

        # Compute similarity matrix of soft- or hard-permuted x
        if mode == "soft":
            similarities_x = self.similarities(x_perm)
        else:
            similarities_x = apply_hard_permutation_batch_to_similarity(
                x=self._similarities_hard_x, perms=perms
            )

        loss = self.effective_similarities_comparison_loss_(
            similarities_x, self._similarities_hard_y
        )

        return {
            "perms": perms,
            "x_perm": x_perm,
            "loss": loss,
        }

    def prepare_fit(self, x: torch.Tensor, y: torch.Tensor) -> None:
        # Validate inputs
        self.validate_inputs(x, y, check_same_alphabet_size=True)

        # Precompute similarity matrices
        self._precompute_similarities(x, y)

    def compute_losses_identity_perm(
        self, x: torch.Tensor, y: torch.Tensor
    ) -> dict[str, float]:
        # Precompute matrices of best hits
        self._precompute_similarities(x, y)

        # Compute hard/soft losses when using identity permutation
        with torch.no_grad():
            hard_loss_identity_perm = self.effective_similarities_comparison_loss_(
                self._similarities_hard_x, self._similarities_hard_y
            ).item()
            soft_loss_identity_perm = hard_loss_identity_perm

        return {"hard": hard_loss_identity_perm, "soft": soft_loss_identity_perm}

# %% ../nbs/train.ipynb 15
class GraphAlignment(DiffPaSSModel):
    """DiffPaSS model for general graph alignment starting from the weighted adjacency matrices of two graphs."""

    are_inputs_msas = False

    def __init__(
        self,
        # Number of graph nodes in each group (e.g. species), assumed the same between the two graphs to align
        group_sizes: Sequence[int],
        # If not ``None``, fixed pairings between groups, of the form [[(i1, j1), (i2, j2), ...], ...] where (i1, j1) are the indices of the first fixed pair in the first group to be paired, etc.
        fixed_pairings: Optional[IndexPairsInGroups] = None,
        # If not ``None``, configuration dictionary containing init parameters for the internal `GeneralizedPermutation` object to compute soft/hard permutations. Soft/hard permutations ``P`` act on adjacency matrices ``X`` via ``P @ X @ P.T``
        permutation_cfg: Optional[dict[str, Any]] = None,
        # If not ``None``, custom callable to compute the differentiable loss between the soft/hard-permuted adjacency matrix of graph ``x`` and the adjacency matrix of graph ``y``. Defaults to dot product between all upper triangular elements
        comparison_loss: Optional[callable] = None,
    ):
        super().__init__()

        # Initialize permutation and matrix apply modules
        # (self.permutation and self.matrix_apply)
        self.init_permutation(
            group_sizes=group_sizes,
            fixed_pairings=fixed_pairings,
            permutation_cfg=permutation_cfg,
        )
        self.permutation_conjugate = PermutationConjugate(group_sizes=self.group_sizes)

        #  Comparison loss
        self.comparison_loss = comparison_loss
        if self.comparison_loss is None:
            # Default: dot product between all upper triangular elements
            self.effective_comparison_loss_ = IntraGroupSimilarityLoss(group_sizes=None)
        else:
            self.effective_comparison_loss_ = self.comparison_loss

    def forward(self, x: torch.Tensor, y: torch.Tensor) -> dict[str, torch.Tensor]:
        mode = self.permutation.mode

        # Soft or hard permutations (list)
        perms = self.permutation()

        # Conjugate adjacency matrix x by soft/hard permutation P: P @ x @ P.T
        if mode == "soft":
            x_perm = self.permutation_conjugate(x, mats=perms)
        else:
            x_perm = apply_hard_permutation_batch_to_similarity(x=x, perms=perms)
        loss = self.effective_comparison_loss_(x_perm, y, mats=perms)

        return {
            "perms": perms,
            "x_perm": x_perm,
            "loss": loss,
        }

    def prepare_fit(self, x: torch.Tensor, y: torch.Tensor) -> None:
        # Validate inputs
        self.validate_inputs(x, y)

    def compute_losses_identity_perm(
        self, x: torch.Tensor, y: torch.Tensor
    ) -> dict[str, float]:
        # Compute hard/soft losses when using identity permutation
        with torch.no_grad():
            hard_loss_identity_perm = self.effective_comparison_loss_(
                x, y, mats=[torch.eye(s).to(x.device) for s in self.group_sizes]
            ).item()
            soft_loss_identity_perm = hard_loss_identity_perm

        return {"hard": hard_loss_identity_perm, "soft": soft_loss_identity_perm}
