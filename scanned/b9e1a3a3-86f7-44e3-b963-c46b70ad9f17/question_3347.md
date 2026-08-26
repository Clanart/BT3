# Q3347: ManualCompound.compound - no reentrancy guard on a function that sweeps balances

## Question
In rewards/ManualCompound.sol, compound() carries no nonReentrant while performing external claims, external converts and external transfers around balance reads, so a token with a transfer hook re-enters between the balance read and the settlement. Does `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` let an unprivileged caller exploit that under the reward token has a transfer hook the caller controls, so that `IERC20(_rewards[i][j]).balanceOf(address(this))` diverges from `the amount this caller actually claimed through multiclaimOnBehalf`, the invariant that a function that settles from live balance reads must hold a reentrancy guard is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: no reentrancy guard on a function that sweeps balances)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: compound() carries no nonReentrant while performing external claims, external converts and external transfers around balance reads, so a token with a transfer hook re-enters between the balance read and the settlement. Precondition: the reward token has a transfer hook the caller controls.
- Invariant to test: a function that settles from live balance reads must hold a reentrancy guard; concretely, `IERC20(_rewards[i][j]).balanceOf(address(this))` must stay reconciled with `the amount this caller actually claimed through multiclaimOnBehalf`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls) under the reward token has a transfer hook the caller controls, asserting on every row that a function that settles from live balance reads must hold a reentrancy guard.
