# Q3143: ManualCompound.compound - compoundableRewards flag is the only filter on the first loop

## Question
In rewards/ManualCompound.sol, the first loop transfers out any caller-named token whose compoundableRewards flag is false, which is the default for every address that was never registered. Starting from a state where no convertor, locker or helper is configured for one of the registered rewards, can an unprivileged EOA use `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` to leave `IERC20(rewards[i].tokenAddress).balanceOf(address(this))` inconsistent with `the caller's own share of that reward token`, violating the invariant that an unregistered token must be rejected rather than treated as freely transferable and extracting Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: compoundableRewards flag is the only filter on the first loop)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: the first loop transfers out any caller-named token whose compoundableRewards flag is false, which is the default for every address that was never registered. Precondition: no convertor, locker or helper is configured for one of the registered rewards.
- Invariant to test: an unregistered token must be rejected rather than treated as freely transferable; concretely, `IERC20(rewards[i].tokenAddress).balanceOf(address(this))` must stay reconciled with `the caller's own share of that reward token`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls) under no convertor, locker or helper is configured for one of the registered rewards, asserting on every row that an unregistered token must be rejected rather than treated as freely transferable.
