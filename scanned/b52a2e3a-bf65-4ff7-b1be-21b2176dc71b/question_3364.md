# Q3364: ManualCompound.compound - rewards array iterated for every caller regardless of what they claimed

## Question
rewards/ManualCompound.sol: the second loop iterates the full rewards array on every call, so every configured reward token is swept on every invocation even when the caller's claim touched none of them. Under the reward token has a transfer hook the caller controls, is there an unprivileged sequence of `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` that leaves `IERC20(rewards[i].tokenAddress).balanceOf(address(this))` unreconciled with `the caller's own share of that reward token`, violates the invariant that settlement must be scoped to the reward tokens the caller's claim actually produced, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: rewards array iterated for every caller regardless of what they claimed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: the second loop iterates the full rewards array on every call, so every configured reward token is swept on every invocation even when the caller's claim touched none of them. Precondition: the reward token has a transfer hook the caller controls.
- Invariant to test: settlement must be scoped to the reward tokens the caller's claim actually produced; concretely, `IERC20(rewards[i].tokenAddress).balanceOf(address(this))` must stay reconciled with `the caller's own share of that reward token`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the reward token has a transfer hook the caller controls, then assert `IERC20(rewards[i].tokenAddress).balanceOf(address(this))` and `the caller's own share of that reward token` end identical in both runs.
