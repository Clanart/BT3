# Q0851: ManualCompound.compound - compoundableRewards flag is the only filter on the first loop

## Question
rewards/ManualCompound.sol: the first loop transfers out any caller-named token whose compoundableRewards flag is false, which is the default for every address that was never registered. Under another user's multiclaimOnBehalf is pending in the mempool and will land in the same block, is there an unprivileged sequence of `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` that leaves `_minRec supplied by the caller` unreconciled with `obtainedmWomAmount in SmartWomConvert`, violates the invariant that an unregistered token must be rejected rather than treated as freely transferable, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: compoundableRewards flag is the only filter on the first loop)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: the first loop transfers out any caller-named token whose compoundableRewards flag is false, which is the default for every address that was never registered. Precondition: another user's multiclaimOnBehalf is pending in the mempool and will land in the same block.
- Invariant to test: an unregistered token must be rejected rather than treated as freely transferable; concretely, `_minRec supplied by the caller` must stay reconciled with `obtainedmWomAmount in SmartWomConvert`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under another user's multiclaimOnBehalf is pending in the mempool and will land in the same block, then assert `_minRec supplied by the caller` and `obtainedmWomAmount in SmartWomConvert` end identical in both runs.
