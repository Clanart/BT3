# Q1159: ManualCompound.compound - fallback branch transfers the whole balance to msg.sender

## Question
rewards/ManualCompound.sol: when no convertor, locker or helper is configured the branch falls through to IERC20(_tokenAddress).safeTransfer(msg.sender, receivedBalance), moving the entire balance out. Under the caller passes an _lps array of pools where they hold no stake at all, is there an unprivileged sequence of `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` that leaves `IERC20(rewards[i].tokenAddress).balanceOf(address(this))` unreconciled with `the caller's own share of that reward token`, violates the invariant that a fallback settlement branch must be bounded by the caller's own entitlement, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: fallback branch transfers the whole balance to msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: when no convertor, locker or helper is configured the branch falls through to IERC20(_tokenAddress).safeTransfer(msg.sender, receivedBalance), moving the entire balance out. Precondition: the caller passes an _lps array of pools where they hold no stake at all.
- Invariant to test: a fallback settlement branch must be bounded by the caller's own entitlement; concretely, `IERC20(rewards[i].tokenAddress).balanceOf(address(this))` must stay reconciled with `the caller's own share of that reward token`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the caller passes an _lps array of pools where they hold no stake at all, snapshot `IERC20(rewards[i].tokenAddress).balanceOf(address(this))` and `the caller's own share of that reward token`, run the attacker's `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
