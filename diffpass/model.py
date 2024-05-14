# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/model.ipynb.

# %% auto 0
__all__ = ['IndexPair', 'IndexPairsInGroup', 'IndexPairsInGroups', 'GeneralizedPermutation', 'MatrixApply',
           'PermutationConjugate', 'global_argmax_from_group_argmaxes', 'apply_hard_permutation_batch_to_similarity',
           'TwoBodyEntropyLoss', 'MILoss', 'HammingSimilarities', 'Blosum62Similarities', 'BestHits',
           'InterGroupSimilarityLoss', 'IntraGroupSimilarityLoss']

# %% ../nbs/model.ipynb 4
# Stdlib imports
from collections.abc import Iterable, Sequence
from typing import Optional, Union, Iterator, Literal
from copy import deepcopy
from warnings import warn
from functools import partial

# NumPy
import numpy as np

# PyTorch
import torch
from torch.nn import Module, ParameterList, Parameter
from torch.nn.functional import softmax

# DiffPaSS imports
from .gumbel_sinkhorn_ops import gumbel_sinkhorn, gumbel_matching
from diffpass.entropy_ops import (
    smooth_mean_one_body_entropy,
    smooth_mean_two_body_entropy,
)
from .constants import get_blosum62_data
from diffpass.sequence_similarity_ops import (
    smooth_hamming_similarities_dot,
    smooth_hamming_similarities_cdist,
    smooth_substitution_matrix_similarities_dot,
    smooth_substitution_matrix_similarities_cdist,
    soft_best_hits,
    hard_best_hits,
)

# Type aliases
IndexPair = tuple[int, int]  # Pair of indices
IndexPairsInGroup = list[IndexPair]  # Pairs of indices in a group of sequences
IndexPairsInGroups = list[IndexPairsInGroup]  # Pairs of indices in groups of sequences

# %% ../nbs/model.ipynb 6
def _consecutive_slices_from_sizes(group_sizes: Optional[Sequence[int]]) -> list[slice]:
    if group_sizes is None:
        return [slice(None)]
    cumsum = np.cumsum(group_sizes).tolist()

    return [slice(start, end) for start, end in zip([0] + cumsum, cumsum)]

# %% ../nbs/model.ipynb 8
class GeneralizedPermutation(Module):
    """Generalized permutation layer implementing both soft and hard permutations."""

    def __init__(
        self,
        *,
        group_sizes: Iterable[int],
        fixed_pairings: Optional[IndexPairsInGroups] = None,
        tau: float = 1.0,
        n_iter: int = 1,
        noise: bool = False,
        noise_factor: float = 1.0,
        noise_std: bool = False,
        mode: Literal["soft", "hard"] = "soft",
    ) -> None:
        super().__init__()
        self.group_sizes = tuple(s for s in group_sizes)

        self.init_fixed_pairings_and_log_alphas(fixed_pairings)

        self.tau = tau
        self.n_iter = n_iter
        self.noise = noise
        self.noise_factor = noise_factor
        self.noise_std = noise_std
        self.mode = mode

    def init_fixed_pairings_and_log_alphas(
        self,
        fixed_pairings: IndexPairsInGroups,
        device: Optional[torch.device] = None,
    ) -> None:
        """Initialize fixed pairings and parameterization matrices."""
        self._validate_fixed_pairings(fixed_pairings)
        self.fixed_pairings = fixed_pairings

        # Initialize parameterization matrices ('log-alphas')
        # By default, initialize all parametrization matrices to zero
        self.nonfixed_group_sizes_ = (
            tuple(
                s - num_efm
                for s, num_efm in zip(
                    self.group_sizes, self._effective_number_fixed_pairings
                )
            )
            if self.fixed_pairings
            else self.group_sizes
        )
        self.log_alphas = ParameterList(
            [
                Parameter(torch.zeros(s, s), requires_grad=bool(s))
                for s in self.nonfixed_group_sizes_
            ]
        )
        self.to(device=device)

    def _validate_fixed_pairings(
        self, fixed_pairings: Optional[IndexPairsInGroups] = None
    ) -> None:
        if fixed_pairings:
            if len(fixed_pairings) != len(self.group_sizes):
                raise ValueError(
                    "If `fixed_pairings` is provided, it must have the same length as "
                    "`group_sizes`."
                )
            for s, fm in zip(self.group_sizes, fixed_pairings):
                if not fm:
                    continue
                if any([len(p) != 2 for p in fm]):
                    raise ValueError(
                        "All fixed pairings must be pairs of indices (i, j)."
                    )
                if any(min(i, j) < 0 or max(i, j) >= s for i, j in fm):
                    raise ValueError(
                        "All fixed pairings must be within the range of the corresponding "
                        "group size."
                    )
            self._effective_number_fixed_pairings = []
            self._effective_fixed_pairings_zip = []
            for idx, (s, fm) in enumerate(zip(self.group_sizes, fixed_pairings)):
                if fm:
                    num_fm = len(fm)
                    fm_zip = list(zip(*fm))
                else:
                    num_fm = 0
                    fm_zip = [(), ()]
                complement = s - num_fm  # Effectively fully fixed when complement <= 1
                is_fully_fixed = complement <= 1
                num_efm = s - (s - num_fm) * (not is_fully_fixed)
                self._effective_number_fixed_pairings.append(num_efm)
                if is_fully_fixed:
                    mask = torch.zeros(s, s, dtype=torch.bool)
                    if complement:
                        possible_idxs = set(range(s))
                        fm_zip[0] += tuple((possible_idxs - set(fm_zip[0])))
                        fm_zip[1] += tuple((possible_idxs - set(fm_zip[1])))
                else:
                    mask = torch.ones(s, s, dtype=torch.bool)
                    for i, j in fm:
                        mask[..., j, :] = False
                        mask[..., :, i] = False
                self.register_buffer(f"_not_fixed_masks_{idx}", mask)
                self._effective_fixed_pairings_zip.append(fm_zip)
            self._total_number_fixed_pairings = sum(
                self._effective_number_fixed_pairings
            )
        else:
            self._effective_fixed_pairings_zip = [[(), ()] for _ in self.group_sizes]
            self._effective_number_fixed_pairings = [0] * len(self.group_sizes)
            self._total_number_fixed_pairings = 0

    @property
    def _not_fixed_masks(self) -> list[torch.Tensor]:
        return [
            getattr(self, f"_not_fixed_masks_{idx}")
            for idx in range(len(self.group_sizes))
        ]

    @property
    def mode(self) -> str:
        return self._mode

    @mode.setter
    def mode(self, value) -> None:
        value = value.lower()
        if value not in ["soft", "hard"]:
            raise ValueError("mode must be either 'soft' or 'hard'.")
        self._mode = value.lower()
        _mats_fn_no_fixed = getattr(self, f"_{self._mode}_mats")
        self._mats_fn = (
            _mats_fn_no_fixed
            if not self.fixed_pairings
            else self._impl_fixed_pairings(_mats_fn_no_fixed)
        )

    def soft_(self) -> None:
        self.mode = "soft"

    def hard_(self) -> None:
        self.mode = "hard"

    def _impl_fixed_pairings(self, func: callable) -> callable:
        """Include fixed matchings in the Gumbel-Sinkhorn or Gumbel-matching operators."""

        def wrapper(gen: Iterator[torch.Tensor]) -> Iterator[torch.Tensor]:
            for s, mat, (row_group, col_group), mask in zip(
                self.group_sizes,
                gen,
                self._effective_fixed_pairings_zip,
                self._not_fixed_masks,
            ):
                mat_all = torch.zeros(
                    s,
                    s,
                    dtype=mat.dtype,
                    layout=mat.layout,
                    device=mat.device,
                )
                # mat_all[j, i] = 1 means that row i becomes row j under a permutation,
                # using our conventions
                mat_all[..., col_group, row_group] = 1
                mat_all.masked_scatter_(mask.to(torch.bool), mat)
                yield mat_all

        return lambda: wrapper(func())

    def _soft_mats(self) -> Iterator[torch.Tensor]:
        """Evaluate the Gumbel-Sinkhorn operator on the current `log_alpha` parameters."""
        return (
            gumbel_sinkhorn(
                log_alpha,
                tau=self.tau,
                n_iter=self.n_iter,
                noise=self.noise,
                noise_factor=self.noise_factor,
                noise_std=self.noise_std,
            )
            for log_alpha in self.log_alphas
        )

    def _hard_mats(self) -> Iterator[torch.Tensor]:
        """Evaluate the Gumbel-matching operator on the current `log_alpha` parameters."""
        return (
            gumbel_matching(
                log_alpha,
                noise=self.noise,
                noise_factor=self.noise_factor,
                noise_std=self.noise_std,
                unbias_lsa=True,
            )
            for log_alpha in self.log_alphas
        )

    def forward(self) -> list[torch.Tensor]:
        """Compute the soft/hard permutations according to ``self._mats_fn.``"""
        mats = self._mats_fn()

        return list(mats)


class MatrixApply(Module):
    """Apply matrices to chunks of a tensor of shape (n_samples, length, alphabet_size)
    and collate the results."""

    def __init__(self, group_sizes: Iterable[int]) -> None:
        super().__init__()
        self.group_sizes = tuple(s for s in group_sizes)
        self._group_slices = _consecutive_slices_from_sizes(self.group_sizes)

    def forward(self, x: torch.Tensor, *, mats: Sequence[torch.Tensor]) -> torch.Tensor:
        out = torch.full_like(x, torch.nan)
        for mats_this_group, sl in zip(mats, self._group_slices):
            out[..., sl, :, :].copy_(
                torch.tensordot(mats_this_group, x[sl, :, :], dims=1)
            )

        return out


class PermutationConjugate(Module):
    """Conjugate blocks of a square 2D tensor of shape (n_samples, n_samples) by
    permutation matrices."""

    def __init__(self, group_sizes: Iterable[int]) -> None:
        super().__init__()
        self.group_sizes = tuple(s for s in group_sizes)
        self._group_slices = _consecutive_slices_from_sizes(self.group_sizes)

    def forward(self, x: torch.Tensor, *, mats: Sequence[torch.Tensor]) -> torch.Tensor:
        out1 = torch.full_like(x, torch.nan)
        out2 = torch.full_like(x, torch.nan)
        # (P * A) * P.T
        for mats_this_group, sl in zip(mats, self._group_slices):
            out1[..., sl, :].copy_(torch.tensordot(mats_this_group, x[sl, :], dims=1))
        for mats_this_group, sl in zip(mats, self._group_slices):
            out2[..., :, sl].copy_(
                torch.tensordot(
                    out1[..., :, sl], mats_this_group.permute((-1, -2)), dims=1
                )
            )

        return out2


def global_argmax_from_group_argmaxes(mats: Iterable[torch.Tensor]) -> torch.Tensor:
    global_argmax = []
    start_idx = 0
    for mats_this_group in mats:
        global_argmax.append(mats_this_group.argmax(-1) + start_idx)
        start_idx += mats_this_group.shape[-1]

    return torch.cat(global_argmax, dim=-1)


def apply_hard_permutation_batch_to_similarity(
    *, x: torch.Tensor, perms: list[torch.Tensor]
) -> torch.Tensor:
    """
    Conjugate a single similarity matrix by a batch of hard permutations.

    Args:
        perms: List of batches of permutation matrices of shape (..., D, D).
        x: Similarity matrix of shape (D, D).

    Returns:
        Batch of conjugated matrices of shape (..., D, D).
    """
    global_argmax = global_argmax_from_group_argmaxes(perms)
    x_permuted_rows = x[global_argmax]

    # Permuting columns is more involved
    index = global_argmax.view(*global_argmax.shape[:-1], 1, -1).expand(
        *global_argmax.shape, global_argmax.shape[-1]
    )
    # Example of gather with 4D tensor and dim=-1:
    # out[i][j][k][l] = input[i][j][k][index[i][j][k][l]]

    return torch.gather(x_permuted_rows, -1, index)

# %% ../nbs/model.ipynb 12
class TwoBodyEntropyLoss(Module):
    """Differentiable extension of the mean of estimated two-body entropies between
    all pairs of columns from two one-hot encoded tensors."""

    def __init__(self):
        super().__init__()

    def forward(self, x: torch.Tensor, y: torch.Tensor) -> torch.Tensor:
        return smooth_mean_two_body_entropy(x, y)


class MILoss(Module):
    """Differentiable extension of minus the mean of estimated mutual informations
    between all pairs of columns from two one-hot encoded tensors."""

    def __init__(self):
        super().__init__()

    def forward(self, x: torch.Tensor, y: torch.Tensor) -> torch.Tensor:
        return smooth_mean_two_body_entropy(x, y) - smooth_mean_one_body_entropy(x)

# %% ../nbs/model.ipynb 17
class HammingSimilarities(Module):
    """Compute Hamming similarities between sequences using differentiable
    operations.

    Optionally, if the sequences are arranged in groups, the computation of
    similarities can be restricted to within groups.
    Differentiable operations are used to compute the similarities, which can be
    either dot products or an L^p distance function."""

    def __init__(
        self,
        *,
        group_sizes: Optional[Iterable[int]] = None,
        use_dot: bool = True,
        p: Optional[float] = None,
    ) -> None:
        super().__init__()
        self.group_sizes = (
            tuple(s for s in group_sizes) if group_sizes is not None else None
        )
        self.use_dot = use_dot
        self.p = p

        if self.use_dot:
            if self.p is not None:
                warn("Since a `p` was provided, `use_dot` will be ignored.")
            self._similarities_fn = smooth_hamming_similarities_dot
            self._similarities_fn_kwargs = {}
        else:
            if self.p is None:
                raise ValueError("If `use_dot` is False, `p` must be provided.")
            self._similarities_fn = smooth_hamming_similarities_cdist
            self._similarities_fn_kwargs = {"p": self.p}

        self._group_slices = _consecutive_slices_from_sizes(self.group_sizes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        size = x.shape[:-3] + (x.shape[-3],) * 2
        out = torch.full(
            size, torch.nan, dtype=x.dtype, layout=x.layout, device=x.device
        )
        for sl in self._group_slices:
            out[..., sl, sl].copy_(
                self._similarities_fn(x[..., sl, :, :], **self._similarities_fn_kwargs)
            )

        return out


class Blosum62Similarities(Module):
    """Compute Blosum62-based similarities between sequences using differentiable
    operations.

    Optionally, if the sequences are arranged in groups, the computation of
    similarities can be restricted to within groups.
    Differentiable operations are used to compute the similarities, which can be
    either dot products or an L^p distance function."""

    def __init__(
        self,
        *,
        group_sizes: Optional[Iterable[int]] = None,
        use_dot: bool = True,
        p: Optional[float] = None,
        use_scoredist: bool = False,
        aa_to_int: Optional[dict[str, int]] = None,
        gaps_as_stars: bool = True,
    ) -> None:
        super().__init__()
        self.group_sizes = (
            tuple(s for s in group_sizes) if group_sizes is not None else None
        )
        self.use_dot = use_dot
        self.p = p
        self.use_scoredist = use_scoredist
        self.aa_to_int = aa_to_int
        self.gaps_as_stars = gaps_as_stars

        blosum62_data = get_blosum62_data(
            aa_to_int=self.aa_to_int, gaps_as_stars=self.gaps_as_stars
        )
        self.register_buffer("subs_mat", blosum62_data.mat)
        self.expected_value = blosum62_data.expected_value

        self._similarities_fn_kwargs = {"subs_mat": self.subs_mat}
        if self.use_dot:
            if self.p is not None:
                warn("Since a `p` was provided, `use_dot` will be ignored.")
            self._similarities_fn = smooth_substitution_matrix_similarities_dot
            self._similarities_fn_kwargs = {
                "use_scoredist": self.use_scoredist,
                "expected_value": self.expected_value,
            }
        else:
            if self.p is None:
                raise ValueError("If `use_dot` is False, `p` must be provided.")
            self._similarities_fn = smooth_substitution_matrix_similarities_cdist
            self._similarities_fn_kwargs = {"p": self.p}

        self._group_slices = _consecutive_slices_from_sizes(self.group_sizes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        size = x.shape[:-3] + (x.shape[-3],) * 2
        out = torch.full(
            size, torch.nan, dtype=x.dtype, layout=x.layout, device=x.device
        )
        for sl in self._group_slices:
            out[..., sl, sl].copy_(
                self._similarities_fn(
                    x[..., sl, :, :],
                    subs_mat=self.subs_mat,
                    **self._similarities_fn_kwargs,
                )
            )

        return out

# %% ../nbs/model.ipynb 22
class BestHits(Module):
    """Compute (reciprocal) best hits within and between groups of sequences,
    starting from a similarity matrix.

    Best hits can be either 'hard', in which cases they are computed using the
    argmax, or 'soft', in which case they are computed using the softmax with a
    temperature parameter `tau`. In both cases, the main diagonal in the similarity
    matrix is excluded by setting its entries to minus infinity."""

    def __init__(
        self,
        *,
        reciprocal: bool = True,
        group_sizes: Optional[Iterable[int]],
        tau: float = 0.1,
        mode: Literal["soft", "hard"] = "soft",
    ) -> None:
        super().__init__()
        self.reciprocal = reciprocal
        self.group_sizes = (
            tuple(s for s in group_sizes) if group_sizes is not None else None
        )
        self._group_slices = _consecutive_slices_from_sizes(self.group_sizes)
        self.tau = tau
        self.mode = mode

    @property
    def mode(self) -> str:
        return self._mode

    @mode.setter
    def mode(self, value) -> None:
        value = value.lower()
        if value not in ["soft", "hard"]:
            raise ValueError("`mode` must be either 'soft' or 'hard'.")
        self._mode = value.lower()
        self._bh_fn = getattr(self, f"_{self._mode}_bh_fn")

    def soft_(self) -> None:
        self.mode = "soft"

    def hard_(self) -> None:
        self.mode = "hard"

    def _soft_bh_fn(self, similarities: torch.Tensor) -> torch.Tensor:
        """Compute soft best hits."""
        return soft_best_hits(
            similarities,
            reciprocal=self.reciprocal,
            group_slices=self._group_slices,
            tau=self.tau,
        )

    def _hard_bh_fn(self, similarities: torch.Tensor) -> torch.Tensor:
        """Compute hard best hits."""
        return hard_best_hits(
            similarities,
            reciprocal=self.reciprocal,
            group_slices=self._group_slices,
        )

    def forward(self, similarities: torch.Tensor) -> torch.Tensor:
        return self._bh_fn(similarities)

# %% ../nbs/model.ipynb 25
class InterGroupSimilarityLoss(Module):
    """Compute a loss that compares similarity matrices restricted to inter-group
    relationships.

    Similarity matrices are expected to be square and symmetric. The loss is computed
    by comparing the (flattened and concatenated) blocks containing inter-group
    similarities."""

    def __init__(
        self,
        *,
        # Number of entries in each group (e.g. species). Groups are assumed to be
        # contiguous in the input similarity matrices
        group_sizes: Iterable[int],
        # If not ``None``, custom callable to compute the differentiable score between
        # the flattened and concatenated inter-group blocks of the similarity matrices.
        # Default: dot product
        score_fn: Union[callable, None] = None,
    ) -> None:
        super().__init__()
        self.group_sizes = tuple(s for s in group_sizes)
        self.score_fn = (
            partial(torch.tensordot, dims=1) if score_fn is None else score_fn
        )

        diag_blocks_mask = torch.block_diag(
            *[torch.ones((s, s), dtype=torch.bool) for s in self.group_sizes]
        )
        self.register_buffer(
            "_upper_no_diag_blocks_mask", torch.triu(~diag_blocks_mask)
        )

    def forward(
        self,
        similarities_x: torch.Tensor,
        similarities_y: torch.Tensor,
        *,
        mats: Optional[Sequence[torch.Tensor]] = None,
    ) -> torch.Tensor:
        # Input validation
        assert similarities_x.ndim >= 2 and similarities_y.ndim >= 2

        scores = self.score_fn(
            similarities_x[..., self._upper_no_diag_blocks_mask],
            similarities_y[..., self._upper_no_diag_blocks_mask],
        )
        loss = -scores

        return loss


class IntraGroupSimilarityLoss(Module):
    """Compute a loss that compares similarity matrices restricted to intra-group
    relationships.

    Similarity matrices are expected to be square and symmetric. Their diagonal
    elements are ignored if `exclude_diagonal` is set to True.
    If `group_sizes` is provided, the loss is computed by comparing the flattened
    and concatenated upper triangular blocks containing intra-group similarities.
    Otherwise, the loss is computed by comparing the upper triangular part of the
    full similarity matrices."""

    def __init__(
        self,
        *,
        # Number of entries in each group (e.g. species). Groups are assumed to be
        # contiguous in the input similarity matrices
        group_sizes: Optional[Iterable[int]] = None,
        # If not ``None``, custom callable to compute the differentiable score between
        # the flattened and concatenated intra-group blocks of the similarity matrices
        # Default: dot product
        score_fn: Union[callable, None] = None,
        # If ``True``, exclude the diagonal elements from the computation
        exclude_diagonal: bool = True,
    ) -> None:
        super().__init__()
        self.group_sizes = (
            tuple(s for s in group_sizes) if group_sizes is not None else None
        )
        self.score_fn = (
            partial(torch.tensordot, dims=1) if score_fn is None else score_fn
        )
        self.exclude_diagonal = exclude_diagonal

        if self.group_sizes is not None:
            # Boolean mask for the main diagonal blocks corresponding to groups
            diag_blocks_mask = torch.block_diag(
                *[torch.ones((s, s), dtype=torch.bool) for s in self.group_sizes]
            )
            # Extract the upper triangular part
            self.register_buffer(
                "_upper_diag_blocks_mask",
                torch.triu(diag_blocks_mask, diagonal=int(self.exclude_diagonal)),
            )
        else:
            self._upper_diag_blocks_mask = None

    def forward(
        self,
        similarities_x: torch.Tensor,
        similarities_y: torch.Tensor,
        *,
        mats: Optional[Sequence[torch.Tensor]] = None,
    ) -> torch.Tensor:
        assert similarities_x.ndim >= 2 and similarities_y.ndim >= 2
        assert similarities_x.shape[-2:] == similarities_x.shape[-2:]

        if self._upper_diag_blocks_mask is None:
            mask = torch.triu(
                torch.ones(
                    similarities_x.shape[-2:],
                    dtype=torch.bool,
                    layout=similarities_x.layout,
                    device=similarities_x.device,
                ),
                diagonal=int(self.exclude_diagonal),
            )
        else:
            mask = self._upper_diag_blocks_mask

        scores = self.score_fn(similarities_x[..., mask], similarities_y[..., mask])
        loss = -scores

        return loss
