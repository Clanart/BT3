# Q0541: ManualCompound.compound - stranded dust from a previous compound is claimable by anyone

## Question
rewards/ManualCompound.sol: any token that a previous compound left behind through rounding, a partial convert or an under-consuming helper stays on the contract until the next caller sweeps the full balance. Under another user's multiclaimOnBehalf is pending in the mempool and will land in the same block, is there an unprivileged sequence of `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` that leaves `_minRec supplied by the caller` unreconciled with `obtainedmWomAmount in SmartWomConvert`, violates the invariant that residual value must be attributed to its owner rather than left claimable by the next caller, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: stranded dust from a previous compound is claimable by anyone)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: any token that a previous compound left behind through rounding, a partial convert or an under-consuming helper stays on the contract until the next caller sweeps the full balance. Precondition: another user's multiclaimOnBehalf is pending in the mempool and will land in the same block.
- Invariant to test: residual value must be attributed to its owner rather than left claimable by the next caller; concretely, `_minRec supplied by the caller` must stay reconciled with `obtainedmWomAmount in SmartWomConvert`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish another user's multiclaimOnBehalf is pending in the mempool and will land in the same block, have the attacker run `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`, then assert the victim's claimable value and the `_minRec supplied by the caller` versus `obtainedmWomAmount in SmartWomConvert` relation are unchanged by the attacker's transaction.
