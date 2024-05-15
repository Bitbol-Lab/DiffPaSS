# Release notes

<!-- do not remove -->

## 0.2.0

### New Features

- Allow for running each iteration in a bootstrap multiple times with different fixed pairs ([#9](https://github.com/Bitbol-Lab/DiffPaSS/issues/9))
  - Implemented with a new `n_repeats` kwarg for `DiffPaSSModel.fit_bootstrap`
  - By performing several repeats of each bootstrap iteration, we can greedily select the best repeat by hard loss, and use that repeat to select the next set of fixed pairs. This should improve performance in hard cases.

- New tutorial notebook on graph alignment, covering `diffpass.train.GraphAlignment` and using `n_repeats` in `fit_boostrap` ([#11](https://github.com/Bitbol-Lab/DiffPaSS/issues/11))

## 0.1.1

### Breaking Changes

- Store hard and soft losses as Python scalars instead of 0-dimensional NumPy arrays ([#3](https://github.com/Bitbol-Lab/DiffPaSS/issues/3))

### New Features

- Unify type annotations for `group_sizes` ([#7](https://github.com/Bitbol-Lab/DiffPaSS/issues/7))

- Add possibility to include diagonals in `IntraGroupSimilarityLoss` computations ([#5](https://github.com/Bitbol-Lab/DiffPaSS/issues/5))

- Store hard and soft losses as Python scalars instead of 0-dimensional NumPy arrays ([#3](https://github.com/Bitbol-Lab/DiffPaSS/issues/3))

### Bugs Squashed

- Fix `fit_bootstrap` appending empty lists ([#1](https://github.com/Bitbol-Lab/DiffPaSS/issues/1))

## 0.1.0

### Breaking Changes

- Change signature of `get_robust_pairs` ([ebb160c](https://github.com/Bitbol-Lab/DiffPaSS/commit/ebb160c512e6aed2cdb9865bdb9b2088a8e0ffd4))

### New Features

- Add `remove_groups_not_in_both` function ([876d017](https://github.com/Bitbol-Lab/DiffPaSS/commit/876d01792a0206ee209478bd2ee5a4c122f2ab9d))

## 0.0.1

- First DiffPaSS public release
