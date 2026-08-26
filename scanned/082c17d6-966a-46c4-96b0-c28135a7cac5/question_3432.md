# Q3432: ManualCompound.compound - empty claim still triggers the sweep

## Question
rewards/ManualCompound.sol: nothing in compound() requires the multiclaimOnBehalf leg to have produced any value, so a caller can pass staking tokens where they hold no stake and still reach both sweep loops. With every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls under attacker control and the caller repeats the call in the same block after a large honest claim, can an unprivileged caller sequence `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` so that `compoundableRewards[token]` and `rewards[i].tokenAddress` no longer reconcile, violating the invariant that a distribution loop must be gated on the value the caller actually generated in the same call and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: empty claim still triggers the sweep)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: nothing in compound() requires the multiclaimOnBehalf leg to have produced any value, so a caller can pass staking tokens where they hold no stake and still reach both sweep loops. Precondition: the caller repeats the call in the same block after a large honest claim.
- Invariant to test: a distribution loop must be gated on the value the caller actually generated in the same call; concretely, `compoundableRewards[token]` must stay reconciled with `rewards[i].tokenAddress`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls) under the caller repeats the call in the same block after a large honest claim, asserting on every row that a distribution loop must be gated on the value the caller actually generated in the same call.
