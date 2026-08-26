# Q1721: ManualCompound.compound - empty claim still triggers the sweep

## Question
Consider rewards/ManualCompound.sol, where nothing in compound() requires the multiclaimOnBehalf leg to have produced any value, so a caller can pass staking tokens where they hold no stake and still reach both sweep loops. Assuming the caller passes empty inner arrays so the claim-all path runs for every pool, can an unprivileged attacker turn this into a divergence between `IERC20(rewards[i].tokenAddress).balanceOf(address(this))` and `the caller's own share of that reward token` via `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`, breaking the invariant that a distribution loop must be gated on the value the caller actually generated in the same call and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: empty claim still triggers the sweep)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: nothing in compound() requires the multiclaimOnBehalf leg to have produced any value, so a caller can pass staking tokens where they hold no stake and still reach both sweep loops. Precondition: the caller passes empty inner arrays so the claim-all path runs for every pool.
- Invariant to test: a distribution loop must be gated on the value the caller actually generated in the same call; concretely, `IERC20(rewards[i].tokenAddress).balanceOf(address(this))` must stay reconciled with `the caller's own share of that reward token`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` sequence atomically under the caller passes empty inner arrays so the claim-all path runs for every pool, asserting at the end that `IERC20(rewards[i].tokenAddress).balanceOf(address(this))` still equals `the caller's own share of that reward token` and the PoC's balance delta is non-positive.
