# Q3381: ManualCompound.compound - compoundableRewards flag is the only filter on the first loop

## Question
rewards/ManualCompound.sol: the first loop transfers out any caller-named token whose compoundableRewards flag is false, which is the default for every address that was never registered. With every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls under attacker control and the reward token has a transfer hook the caller controls, can an unprivileged caller sequence `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` so that `compoundableRewards[token]` and `rewards[i].tokenAddress` no longer reconcile, violating the invariant that an unregistered token must be rejected rather than treated as freely transferable and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: compoundableRewards flag is the only filter on the first loop)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: the first loop transfers out any caller-named token whose compoundableRewards flag is false, which is the default for every address that was never registered. Precondition: the reward token has a transfer hook the caller controls.
- Invariant to test: an unregistered token must be rejected rather than treated as freely transferable; concretely, `compoundableRewards[token]` must stay reconciled with `rewards[i].tokenAddress`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the reward token has a transfer hook the caller controls, snapshot `compoundableRewards[token]` and `rewards[i].tokenAddress`, run the attacker's `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
