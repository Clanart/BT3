# Q0107: ManualCompound.compound - stranded dust from a previous compound is claimable by anyone

## Question
In rewards/ManualCompound.sol, any token that a previous compound left behind through rounding, a partial convert or an under-consuming helper stays on the contract until the next caller sweeps the full balance. Does `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` let an unprivileged caller exploit that under a previous compound left a residue of one of the configured reward tokens on the contract, so that `_convertRatio supplied by the caller` diverges from `the value being converted for other users`, the invariant that residual value must be attributed to its owner rather than left claimable by the next caller is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: stranded dust from a previous compound is claimable by anyone)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: any token that a previous compound left behind through rounding, a partial convert or an under-consuming helper stays on the contract until the next caller sweeps the full balance. Precondition: a previous compound left a residue of one of the configured reward tokens on the contract.
- Invariant to test: residual value must be attributed to its owner rather than left claimable by the next caller; concretely, `_convertRatio supplied by the caller` must stay reconciled with `the value being converted for other users`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` sequence atomically under a previous compound left a residue of one of the configured reward tokens on the contract, asserting at the end that `_convertRatio supplied by the caller` still equals `the value being converted for other users` and the PoC's balance delta is non-positive.
