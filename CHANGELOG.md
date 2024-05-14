# Release notes

<!-- do not remove -->

## 0.1.1
### Breaking Changes

- Store hard and soft losses as Python scalars instead of 0-dimensional NumPy arrays ([#3](https://github.com/Bitbol-Lab/DiffPaSS/issues/3))

### New Features

- Unify type annotations for `group_sizes` ([#7](https://github.com/Bitbol-Lab/DiffPaSS/issues/7))

- Add possibility to include diagonals in `IntraGroupSimilarityLoss` computations ([#5](https://github.com/Bitbol-Lab/DiffPaSS/issues/5))

- Store hard and soft losses as Python scalars instead of 0-dimensional NumPy arrays ([#3](https://github.com/Bitbol-Lab/DiffPaSS/issues/3))

### Bugs Squashed

- Fix `fit_bootstrap` appending empty lists ([#1](https://github.com/Bitbol-Lab/DiffPaSS/issues/1))

>## 0.1.0
### Breaking Changes

- Change signature of `get_robust_pairs` ([ebb160c](https://github.com/Bitbol-Lab/DiffPaSS/commit/ebb160c512e6aed2cdb9865bdb9b2088a8e0ffd4))

### New Features

- Add `remove_groups_not_in_both` function ([876d017](https://github.com/Bitbol-Lab/DiffPaSS/commit/876d01792a0206ee209478bd2ee5a4c122f2ab9d))

>## 0.0.1

- First DiffPaSS public release
