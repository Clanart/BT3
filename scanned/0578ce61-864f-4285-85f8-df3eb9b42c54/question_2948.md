# Q2948: ManualCompound.compound - empty claim still triggers the sweep

## Question
In rewards/ManualCompound.sol, nothing in compound() requires the multiclaimOnBehalf leg to have produced any value, so a caller can pass staking tokens where they hold no stake and still reach both sweep loops. Does `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` let an unprivileged caller exploit that under no convertor, locker or helper is configured for one of the registered rewards, so that `IERC20(_rewards[i][j]).balanceOf(address(this))` diverges from `the amount this caller actually claimed through multiclaimOnBehalf`, the invariant that a distribution loop must be gated on the value the caller actually generated in the same call is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: empty claim still triggers the sweep)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: nothing in compound() requires the multiclaimOnBehalf leg to have produced any value, so a caller can pass staking tokens where they hold no stake and still reach both sweep loops. Precondition: no convertor, locker or helper is configured for one of the registered rewards.
- Invariant to test: a distribution loop must be gated on the value the caller actually generated in the same call; concretely, `IERC20(_rewards[i][j]).balanceOf(address(this))` must stay reconciled with `the amount this caller actually claimed through multiclaimOnBehalf`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under no convertor, locker or helper is configured for one of the registered rewards, then assert `IERC20(_rewards[i][j]).balanceOf(address(this))` and `the amount this caller actually claimed through multiclaimOnBehalf` end identical in both runs.
