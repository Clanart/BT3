# Q3449: ManualCompound.compound - stranded dust from a previous compound is claimable by anyone

## Question
In rewards/ManualCompound.sol, any token that a previous compound left behind through rounding, a partial convert or an under-consuming helper stays on the contract until the next caller sweeps the full balance. Starting from a state where the caller repeats the call in the same block after a large honest claim, can an unprivileged EOA use `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` to leave `_convertRatio supplied by the caller` inconsistent with `the value being converted for other users`, violating the invariant that residual value must be attributed to its owner rather than left claimable by the next caller and extracting Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: stranded dust from a previous compound is claimable by anyone)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: any token that a previous compound left behind through rounding, a partial convert or an under-consuming helper stays on the contract until the next caller sweeps the full balance. Precondition: the caller repeats the call in the same block after a large honest claim.
- Invariant to test: residual value must be attributed to its owner rather than left claimable by the next caller; concretely, `_convertRatio supplied by the caller` must stay reconciled with `the value being converted for other users`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the caller repeats the call in the same block after a large honest claim, then assert `_convertRatio supplied by the caller` and `the value being converted for other users` end identical in both runs.
