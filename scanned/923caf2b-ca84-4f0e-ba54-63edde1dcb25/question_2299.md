# Q2299: ManualCompound.compound - compoundableRewards flag is the only filter on the first loop

## Question
rewards/ManualCompound.sol: the first loop transfers out any caller-named token whose compoundableRewards flag is false, which is the default for every address that was never registered. Under the configured convertor is SmartWomConvert and _convertRatio is set to zero, is there an unprivileged sequence of `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` that leaves `_convertRatio supplied by the caller` unreconciled with `the value being converted for other users`, violates the invariant that an unregistered token must be rejected rather than treated as freely transferable, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: compoundableRewards flag is the only filter on the first loop)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: the first loop transfers out any caller-named token whose compoundableRewards flag is false, which is the default for every address that was never registered. Precondition: the configured convertor is SmartWomConvert and _convertRatio is set to zero.
- Invariant to test: an unregistered token must be rejected rather than treated as freely transferable; concretely, `_convertRatio supplied by the caller` must stay reconciled with `the value being converted for other users`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls) under the configured convertor is SmartWomConvert and _convertRatio is set to zero, asserting on every row that an unregistered token must be rejected rather than treated as freely transferable.
