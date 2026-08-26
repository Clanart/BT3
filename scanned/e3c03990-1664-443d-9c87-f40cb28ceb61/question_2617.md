# Q2617: ManualCompound.compound - compoundableRewards flag is the only filter on the first loop

## Question
In rewards/ManualCompound.sol, the first loop transfers out any caller-named token whose compoundableRewards flag is false, which is the default for every address that was never registered. Does `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` let an unprivileged caller exploit that under the configured convertor is SmartWomConvert and _minRec is set to zero, so that `_minRec supplied by the caller` diverges from `obtainedmWomAmount in SmartWomConvert`, the invariant that an unregistered token must be rejected rather than treated as freely transferable is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: compoundableRewards flag is the only filter on the first loop)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: the first loop transfers out any caller-named token whose compoundableRewards flag is false, which is the default for every address that was never registered. Precondition: the configured convertor is SmartWomConvert and _minRec is set to zero.
- Invariant to test: an unregistered token must be rejected rather than treated as freely transferable; concretely, `_minRec supplied by the caller` must stay reconciled with `obtainedmWomAmount in SmartWomConvert`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the configured convertor is SmartWomConvert and _minRec is set to zero, then assert `_minRec supplied by the caller` and `obtainedmWomAmount in SmartWomConvert` end identical in both runs.
